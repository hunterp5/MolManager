"""SQLite-backed row store with paged reads and simple filter/sort pushdown."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path


def _quoted_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


class SqliteTableStore:
    """Materialize row dicts in SQLite and query them in pages."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            self._path = Path(tempfile.gettempdir()) / "MOLMANAGER_table_cache.sqlite3"
        else:
            self._path = Path(db_path)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._headers: list[str] = []

    @property
    def headers(self) -> list[str]:
        return list(self._headers)

    def close(self) -> None:
        self._conn.close()

    def rebuild(self, headers: list[str], rows: list[tuple[int, dict[str, str]]]) -> None:
        cols = [h for h in headers if h not in ("ID_HIDDEN", "Structure")]
        self._headers = list(cols)
        cur = self._conn.cursor()
        cur.execute("DROP TABLE IF EXISTS table_rows")
        col_sql = ", ".join(f"{_quoted_ident(h)} TEXT" for h in cols)
        if col_sql:
            cur.execute(f"CREATE TABLE table_rows (oid INTEGER PRIMARY KEY, {col_sql})")
        else:
            cur.execute("CREATE TABLE table_rows (oid INTEGER PRIMARY KEY)")
        if rows:
            names = ["oid"] + cols
            placeholders = ", ".join(["?"] * len(names))
            insert_sql = (
                f"INSERT INTO table_rows ({', '.join(_quoted_ident(n) for n in names)}) VALUES ({placeholders})"
            )
            payload = []
            for oid, cells in rows:
                vals = [int(oid)] + [str(cells.get(h, "") or "") for h in cols]
                payload.append(vals)
            cur.executemany(insert_sql, payload)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_table_rows_oid ON table_rows(oid)")
        self._conn.commit()

    def count(self, where_sql: str = "", args: tuple | list | None = None) -> int:
        cur = self._conn.cursor()
        args = tuple(args or ())
        sql = "SELECT COUNT(*) AS c FROM table_rows"
        if where_sql:
            sql += f" WHERE {where_sql}"
        row = cur.execute(sql, args).fetchone()
        return int(row["c"]) if row is not None else 0

    def distinct_values(self, column: str, *, limit: int = 2001) -> list[str]:
        """Distinct non-null text values for a column (for category filter UI)."""
        if column not in self._headers:
            return []
        lim = max(1, int(limit))
        qp = _quoted_ident(column)
        sql = f"SELECT DISTINCT {qp} AS v FROM table_rows ORDER BY LOWER({qp}) ASC, {qp} ASC LIMIT ?"
        rows = self._conn.execute(sql, (lim,)).fetchall()
        return [str(rec["v"] or "") for rec in rows]

    def fetch_page(
        self,
        *,
        limit: int,
        offset: int = 0,
        where_sql: str = "",
        args: tuple | list | None = None,
        sort_by: str = "oid",
        ascending: bool = True,
    ) -> list[tuple[int, dict[str, str]]]:
        lim = max(1, int(limit))
        off = max(0, int(offset))
        args = tuple(args or ())
        col = sort_by if sort_by in self._headers else "oid"
        order = "ASC" if ascending else "DESC"
        sql = "SELECT * FROM table_rows"
        if where_sql:
            sql += f" WHERE {where_sql}"
        sql += f" ORDER BY {_quoted_ident(col)} {order}, oid {order} LIMIT ? OFFSET ?"
        rows = self._conn.execute(sql, args + (lim, off)).fetchall()
        out: list[tuple[int, dict[str, str]]] = []
        for rec in rows:
            oid = int(rec["oid"])
            out.append((oid, {h: str(rec[h] or "") for h in self._headers}))
        return out

