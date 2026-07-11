"""Row-text backing stores for :class:`CompoundTableModel`.

The model keeps pixmaps, color caches, numeric-bounds caches and headers in RAM; the *row store*
owns only the ordered rows and their per-column text. Isolating that behind an interface lets a
large table swap the default in-memory list for a disk-backed store without changing the model.

``InMemoryRowStore`` preserves the exact previous ``list[_Row]`` semantics (identity, order, sparse
``values`` dicts). All indices are dense logical positions ``0..len-1`` in current display order.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _Row:
    oid: int
    values: dict[str, str] = field(default_factory=dict)


class InMemoryRowStore:
    """Ordered rows of ``oid`` + sparse ``{header: text}`` held entirely in RAM."""

    def __init__(self) -> None:
        self._rows: list[_Row] = []
        self._oid_to_row: dict[int, int] = {}

    # --- counts / order ---------------------------------------------------
    def __len__(self) -> int:
        return len(self._rows)

    def clear(self) -> None:
        self._rows.clear()
        self._oid_to_row.clear()

    def oid_at(self, index: int) -> int:
        return self._rows[index].oid

    def all_oids(self) -> list[int]:
        return [r.oid for r in self._rows]

    def _rebuild_index(self) -> None:
        self._oid_to_row = {row.oid: i for i, row in enumerate(self._rows)}

    def index_of(self, oid: int) -> int:
        """Logical row for *oid*, self-healing the index if stale (-1 if absent)."""
        r = self._oid_to_row.get(oid, -1)
        if 0 <= r < len(self._rows) and self._rows[r].oid == oid:
            return r
        self._rebuild_index()
        return self._oid_to_row.get(oid, -1)

    def index_of_fast(self, oid: int) -> int:
        """Non-healing lookup for hot bulk paths (-1 if absent); caller ensures index is fresh."""
        return self._oid_to_row.get(int(oid), -1)

    # --- reads ------------------------------------------------------------
    def value_at(self, index: int, header: str, default: str = "") -> str:
        return self._rows[index].values.get(header, default)

    def value_by_oid(self, oid: int, header: str, default: str = "") -> str:
        r = self.index_of(oid)
        if r < 0:
            return default
        return self._rows[r].values.get(header, default)

    def iter_values(self, header: str):
        """Yield the raw text (or ``""``) for *header* across all rows in display order."""
        for row in self._rows:
            yield row.values.get(header, "")

    def snapshot_pairs(self, header: str | None) -> list[tuple[int, str]]:
        """``(oid, text)`` per row; ``header=None`` yields empty text (id/structure columns)."""
        if header is None:
            return [(r.oid, "") for r in self._rows]
        return [(r.oid, r.values.get(header, "") or "") for r in self._rows]

    def column_by_oid(self, header: str) -> dict[int, str]:
        return {row.oid: str(row.values.get(header, "")) for row in self._rows}

    def export_slice(self, headers: list[str], lo: int, hi: int) -> list[tuple[int, dict[str, str]]]:
        out: list[tuple[int, dict[str, str]]] = []
        for row in self._rows[lo:hi]:
            cells = {h: str(row.values.get(h, "") or "") for h in headers}
            out.append((int(row.oid), cells))
        return out

    def export_all(self) -> list[tuple[int, dict[str, str]]]:
        """Every row's full ``(oid, cells)`` in display order (for migration to a disk store)."""
        return [(row.oid, row.values) for row in self._rows]

    # --- inserts ----------------------------------------------------------
    def append(self, oid: int, cells: dict[str, str]) -> int:
        idx = len(self._rows)
        self._rows.append(_Row(oid=int(oid), values=dict(cells)))
        self._oid_to_row[int(oid)] = idx
        return idx

    def append_batch(self, entries: list[tuple[int, dict[str, str]]]) -> tuple[int, int]:
        """Append many rows; return the inserted ``(start, end)`` inclusive index range."""
        start = len(self._rows)
        idx = start
        for oid, cells in entries:
            self._rows.append(_Row(oid=int(oid), values=dict(cells)))
            self._oid_to_row[int(oid)] = idx
            idx += 1
        return start, idx - 1

    def insert_at(self, index: int, oid: int, cells: dict[str, str]) -> None:
        self._rows.insert(index, _Row(oid=int(oid), values=dict(cells)))
        self._rebuild_index()

    def insert_many_at(self, ordered_entries: list[tuple[int, int, dict[str, str]]]) -> None:
        """Insert ``(orig_index, oid, cells)`` items (pre-sorted by orig_index) with offset ``k``."""
        new_rows = list(self._rows)
        for k, (orig_row, oid, cells) in enumerate(ordered_entries):
            insert_at = max(0, min(int(orig_row) + k, len(new_rows)))
            new_rows.insert(insert_at, _Row(oid=int(oid), values=dict(cells)))
        self._rows = new_rows
        self._rebuild_index()

    # --- removes ----------------------------------------------------------
    def remove_at(self, index: int) -> int:
        oid = self._rows[index].oid
        self._rows.pop(index)
        self._rebuild_index()
        return int(oid)

    def remove_by_oids(self, kill: set[int]) -> int:
        n_before = len(self._rows)
        self._rows = [r for r in self._rows if int(r.oid) not in kill]
        self._rebuild_index()
        return n_before - len(self._rows)

    # --- cell / column writes --------------------------------------------
    def set_value_by_oid(self, oid: int, header: str, text: str) -> int:
        r = self.index_of(oid)
        if r < 0:
            return -1
        self._rows[r].values[header] = text
        return r

    def set_values_by_oid(self, oid: int, mapping: dict[str, str]) -> int:
        r = self.index_of(oid)
        if r < 0:
            return -1
        row_values = self._rows[r].values
        for header, text in mapping.items():
            row_values[header] = text
        return r

    def set_value_at(self, index: int, header: str, text: str) -> None:
        self._rows[index].values[header] = text

    def set_column_by_oids(self, header: str, oid_values: list[tuple[int, str]]) -> list[int]:
        changed: list[int] = []
        for oid, text in oid_values:
            r = self.index_of(oid)
            if r < 0:
                continue
            self._rows[r].values[header] = str(text)
            changed.append(r)
        return changed

    def apply_columns_bulk(
        self, headers: set[str], oid_value_rows: list[tuple[int, dict[str, str]]]
    ) -> list[int]:
        changed: list[int] = []
        for oid, row_d in oid_value_rows:
            r = self.index_of_fast(oid)
            if r < 0 or r >= len(self._rows):
                continue
            row_values = self._rows[r].values
            for header in headers:
                if header in row_d:
                    row_values[header] = str(row_d[header])
            changed.append(r)
        return changed

    def fill_column(self, header: str, oid_to_text: dict[int, str], default: str = "") -> None:
        for row in self._rows:
            row.values[header] = oid_to_text.get(row.oid, default)

    # --- column structure -------------------------------------------------
    def add_column(self, header: str, copy_from: str | None = None) -> None:
        if copy_from is None:
            return
        for row in self._rows:
            row.values[header] = row.values.get(copy_from, "")

    def remove_column(self, header: str) -> None:
        for row in self._rows:
            row.values.pop(header, None)

    def rename_column(self, old: str, new: str) -> None:
        for row in self._rows:
            if old in row.values:
                row.values[new] = row.values.pop(old)

    # --- reorder ----------------------------------------------------------
    def reorder(self, ordered_oids: list[int]) -> None:
        pos = {int(oid): i for i, oid in enumerate(ordered_oids)}
        tail = len(pos)
        self._rows.sort(key=lambda r: pos.get(r.oid, tail))
        self._rebuild_index()

    def move(self, src_index: int, dest_index: int) -> None:
        row = self._rows.pop(src_index)
        self._rows.insert(dest_index, row)
        self._rebuild_index()


# SQLite parameter limit is ~999; keep IN-clause / executemany batches under it.
_SQL_BATCH = 900


def _dumps(cells: dict[str, str]) -> bytes:
    return json.dumps(cells, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _loads(blob) -> dict[str, str]:
    if not blob:
        return {}
    try:
        return json.loads(bytes(blob).decode("utf-8"))
    except Exception:
        return {}


class SqliteRowStore:
    """Row store that keeps only the oid order vector + a bounded LRU of rows in RAM.

    Cell text lives in a temporary SQLite file (one JSON blob per row), so steady-state RAM is
    ``O(row_count)`` for the order/index vectors plus the bounded row cache — independent of the
    number of columns or their text length. Display order is held in RAM (``_order``); the database
    is unordered and keyed by ``oid``. Must be used from a single thread (the GUI thread); scans
    stream from the DB and callers receive materialized snapshots.
    """

    def __init__(self, *, cache_rows: int = 20_000) -> None:
        self._order: list[int] = []
        self._pos: dict[int, int] = {}
        self._cache: OrderedDict[int, dict[str, str]] = OrderedDict()
        self._cache_cap = max(0, int(cache_rows))
        self._conn: sqlite3.Connection | None = None
        self._path: str | None = None

    # --- disk backend -----------------------------------------------------
    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        fd, path = tempfile.mkstemp(prefix="molmanager_rows_", suffix=".sqlite")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("CREATE TABLE IF NOT EXISTS rows (oid INTEGER PRIMARY KEY, data BLOB)")
        conn.commit()
        self._conn = conn
        self._path = path
        return conn

    def _rebuild_pos(self) -> None:
        self._pos = {oid: i for i, oid in enumerate(self._order)}

    def _cache_put(self, oid: int, cells: dict[str, str]) -> None:
        if self._cache_cap <= 0:
            return
        self._cache[oid] = cells
        self._cache.move_to_end(oid)
        while len(self._cache) > self._cache_cap:
            self._cache.popitem(last=False)

    def _row(self, oid: int) -> dict[str, str]:
        """Return the (mutable, cached) cell dict for *oid* (empty dict if absent)."""
        cached = self._cache.get(oid)
        if cached is not None:
            self._cache.move_to_end(oid)
            return cached
        conn = self._ensure_conn()
        cur = conn.execute("SELECT data FROM rows WHERE oid = ?", (int(oid),))
        row = cur.fetchone()
        cells = _loads(row[0]) if row else {}
        self._cache_put(oid, cells)
        return cells

    def _write_rows(self, items: list[tuple[int, dict[str, str]]]) -> None:
        if not items:
            return
        conn = self._ensure_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO rows (oid, data) VALUES (?, ?)",
            [(int(oid), sqlite3.Binary(_dumps(cells))) for oid, cells in items],
        )
        conn.commit()

    def _iter_all(self):
        """Yield ``(oid, cells)`` for every row straight from the DB (order unspecified)."""
        if self._conn is None:
            return
        for oid, data in self._conn.execute("SELECT oid, data FROM rows"):
            yield int(oid), _loads(data)

    def _fetch_many(self, oids: list[int]) -> dict[int, dict[str, str]]:
        out: dict[int, dict[str, str]] = {}
        if not oids:
            return out
        conn = self._ensure_conn()
        for i in range(0, len(oids), _SQL_BATCH):
            chunk = oids[i : i + _SQL_BATCH]
            placeholders = ",".join("?" * len(chunk))
            cur = conn.execute(
                f"SELECT oid, data FROM rows WHERE oid IN ({placeholders})",
                [int(o) for o in chunk],
            )
            for oid, data in cur:
                out[int(oid)] = _loads(data)
        return out

    # --- counts / order ---------------------------------------------------
    def __len__(self) -> int:
        return len(self._order)

    def clear(self) -> None:
        self._order.clear()
        self._pos.clear()
        self._cache.clear()
        conn = self._conn
        self._conn = None
        path = self._path
        self._path = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    def oid_at(self, index: int) -> int:
        return self._order[index]

    def all_oids(self) -> list[int]:
        return list(self._order)

    def index_of(self, oid: int) -> int:
        return self._pos.get(int(oid), -1)

    def index_of_fast(self, oid: int) -> int:
        return self._pos.get(int(oid), -1)

    # --- reads ------------------------------------------------------------
    def value_at(self, index: int, header: str, default: str = "") -> str:
        return self._row(self._order[index]).get(header, default)

    def value_by_oid(self, oid: int, header: str, default: str = "") -> str:
        if int(oid) not in self._pos:
            return default
        return self._row(int(oid)).get(header, default)

    def iter_values(self, header: str):
        for _oid, cells in self._iter_all():
            yield cells.get(header, "")

    def snapshot_pairs(self, header: str | None) -> list[tuple[int, str]]:
        if header is None:
            return [(oid, "") for oid in self._order]
        by_oid = {oid: cells.get(header, "") or "" for oid, cells in self._iter_all()}
        return [(oid, by_oid.get(oid, "")) for oid in self._order]

    def column_by_oid(self, header: str) -> dict[int, str]:
        return {oid: str(cells.get(header, "")) for oid, cells in self._iter_all()}

    def export_slice(self, headers: list[str], lo: int, hi: int) -> list[tuple[int, dict[str, str]]]:
        oids = self._order[lo:hi]
        fetched = self._fetch_many(oids)
        out: list[tuple[int, dict[str, str]]] = []
        for oid in oids:
            cells = fetched.get(oid, {})
            out.append((int(oid), {h: str(cells.get(h, "") or "") for h in headers}))
        return out

    # --- inserts ----------------------------------------------------------
    def append(self, oid: int, cells: dict[str, str]) -> int:
        oid = int(oid)
        idx = len(self._order)
        self._order.append(oid)
        self._pos[oid] = idx
        row_cells = dict(cells)
        self._write_rows([(oid, row_cells)])
        self._cache_put(oid, row_cells)
        return idx

    def append_batch(self, entries: list[tuple[int, dict[str, str]]]) -> tuple[int, int]:
        start = len(self._order)
        items: list[tuple[int, dict[str, str]]] = []
        idx = start
        for oid, cells in entries:
            oid = int(oid)
            self._order.append(oid)
            self._pos[oid] = idx
            idx += 1
            items.append((oid, dict(cells)))
        self._write_rows(items)
        return start, idx - 1

    def insert_at(self, index: int, oid: int, cells: dict[str, str]) -> None:
        oid = int(oid)
        self._order.insert(index, oid)
        self._rebuild_pos()
        row_cells = dict(cells)
        self._write_rows([(oid, row_cells)])
        self._cache_put(oid, row_cells)

    def insert_many_at(self, ordered_entries: list[tuple[int, int, dict[str, str]]]) -> None:
        new_order = list(self._order)
        items: list[tuple[int, dict[str, str]]] = []
        for k, (orig_row, oid, cells) in enumerate(ordered_entries):
            oid = int(oid)
            insert_at = max(0, min(int(orig_row) + k, len(new_order)))
            new_order.insert(insert_at, oid)
            items.append((oid, dict(cells)))
        self._order = new_order
        self._rebuild_pos()
        self._write_rows(items)

    # --- removes ----------------------------------------------------------
    def _delete_oids(self, kill: set[int]) -> None:
        conn = self._ensure_conn()
        oids = [int(o) for o in kill]
        for i in range(0, len(oids), _SQL_BATCH):
            chunk = oids[i : i + _SQL_BATCH]
            placeholders = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM rows WHERE oid IN ({placeholders})", chunk)
        conn.commit()
        for oid in kill:
            self._cache.pop(oid, None)

    def remove_at(self, index: int) -> int:
        oid = int(self._order[index])
        self._order.pop(index)
        self._rebuild_pos()
        self._delete_oids({oid})
        return oid

    def remove_by_oids(self, kill: set[int]) -> int:
        kill = {int(x) for x in kill}
        n_before = len(self._order)
        self._order = [oid for oid in self._order if oid not in kill]
        self._rebuild_pos()
        self._delete_oids(kill)
        return n_before - len(self._order)

    # --- cell / column writes --------------------------------------------
    def set_value_by_oid(self, oid: int, header: str, text: str) -> int:
        oid = int(oid)
        r = self._pos.get(oid, -1)
        if r < 0:
            return -1
        cells = self._row(oid)
        cells[header] = text
        self._write_rows([(oid, cells)])
        return r

    def set_values_by_oid(self, oid: int, mapping: dict[str, str]) -> int:
        oid = int(oid)
        r = self._pos.get(oid, -1)
        if r < 0:
            return -1
        cells = self._row(oid)
        for header, text in mapping.items():
            cells[header] = text
        self._write_rows([(oid, cells)])
        return r

    def set_value_at(self, index: int, header: str, text: str) -> None:
        oid = int(self._order[index])
        cells = self._row(oid)
        cells[header] = text
        self._write_rows([(oid, cells)])

    def set_column_by_oids(self, header: str, oid_values: list[tuple[int, str]]) -> list[int]:
        changed: list[int] = []
        updates: list[tuple[int, dict[str, str]]] = []
        for oid, text in oid_values:
            oid = int(oid)
            r = self._pos.get(oid, -1)
            if r < 0:
                continue
            cells = self._row(oid)
            cells[header] = str(text)
            updates.append((oid, cells))
            changed.append(r)
        self._write_rows(updates)
        return changed

    def apply_columns_bulk(
        self, headers: set[str], oid_value_rows: list[tuple[int, dict[str, str]]]
    ) -> list[int]:
        changed: list[int] = []
        updates: list[tuple[int, dict[str, str]]] = []
        for oid, row_d in oid_value_rows:
            oid = int(oid)
            r = self._pos.get(oid, -1)
            if r < 0:
                continue
            cells = self._row(oid)
            for header in headers:
                if header in row_d:
                    cells[header] = str(row_d[header])
            updates.append((oid, cells))
            changed.append(r)
        self._write_rows(updates)
        return changed

    def fill_column(self, header: str, oid_to_text: dict[int, str], default: str = "") -> None:
        updates: list[tuple[int, dict[str, str]]] = []
        for oid, cells in self._iter_all():
            cells[header] = oid_to_text.get(oid, default)
            updates.append((oid, cells))
        self._write_rows(updates)
        self._cache.clear()

    # --- column structure -------------------------------------------------
    def add_column(self, header: str, copy_from: str | None = None) -> None:
        if copy_from is None:
            return
        updates = [(oid, {**cells, header: cells.get(copy_from, "")}) for oid, cells in self._iter_all()]
        self._write_rows(updates)
        self._cache.clear()

    def remove_column(self, header: str) -> None:
        updates: list[tuple[int, dict[str, str]]] = []
        for oid, cells in self._iter_all():
            if header in cells:
                cells.pop(header, None)
                updates.append((oid, cells))
        self._write_rows(updates)
        self._cache.clear()

    def rename_column(self, old: str, new: str) -> None:
        updates: list[tuple[int, dict[str, str]]] = []
        for oid, cells in self._iter_all():
            if old in cells:
                cells[new] = cells.pop(old)
                updates.append((oid, cells))
        self._write_rows(updates)
        self._cache.clear()

    # --- reorder ----------------------------------------------------------
    def reorder(self, ordered_oids: list[int]) -> None:
        pos = {int(oid): i for i, oid in enumerate(ordered_oids)}
        tail = len(pos)
        self._order.sort(key=lambda oid: pos.get(oid, tail))
        self._rebuild_pos()

    def move(self, src_index: int, dest_index: int) -> None:
        oid = self._order.pop(src_index)
        self._order.insert(dest_index, oid)
        self._rebuild_pos()
