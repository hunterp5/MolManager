"""
QAbstractTableModel + QTableView stack for compound tables.

The model owns row text, optional structure pixmaps, and stable molecule ids; a delegate
paints the Structure column.

Run the standalone demo::

    python -m chemmanager.table_model_demo
    python -m chemmanager.ui.compound_table_model
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PyQt5.QtCore import QAbstractItemModel, QAbstractTableModel, QModelIndex, QRect, QSize, Qt
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtWidgets import QHeaderView, QStyledItemDelegate, QStyleOptionViewItem, QTableView

from ..display_constants import (
    STRUCTURE_COLUMN_HORIZONTAL_PADDING,
    STRUCTURE_DEPICT_HEIGHT,
    STRUCTURE_DEPICT_WIDTH,
    STRUCTURE_ROW_DEFAULT_HEIGHT,
)
from ..utils import safe_float
from .strings import STRUCTURE_PENDING_HINT

# Backward-compatible names (also re-export layout constants for ``from .compound_table_model import …``).
STRUCTURE_COLUMN_PENDING_HINT = STRUCTURE_PENDING_HINT

# Packed multi-conformer / alignment payloads — not numeric filter columns (avoids scanning huge cells).
_NON_NUMERIC_BLOB_COLUMNS = frozenset({"confs", "superpose"})


@dataclass
class _Row:
    oid: int
    values: dict[str, str] = field(default_factory=dict)


class CompoundTableModel(QAbstractTableModel):
    """
    Column 0: hidden id (string of oid).
    Column 1: Structure (pixmap via DecorationRole, or placeholder).
    Column 2..n: text from ``values`` keyed by header name.
    """

    STRUCTURE_COL = 1

    def __init__(self, headers: list[str], parent=None):
        super().__init__(parent)
        self._headers = list(headers)
        self._rows: list[_Row] = []
        self._pixmaps: dict[int, QPixmap] = {}
        self._oid_to_row: dict[int, int] = {}
        # Optional extra columns that show a 2D pixmap (e.g. disconnected fragment) keyed by (oid, header).
        self._pixmap_columns: set[str] = set()
        self._extra_pixmaps: dict[tuple[int, str], QPixmap] = {}
        # Incremental numeric min/max cache for filter sliders (see numeric_bounds_by_column).
        self._numeric_bounds_cache: dict[str, dict] | None = None
        self._numeric_bounds_key: tuple[str, ...] | None = None  # sorted data header names used for last full build
        self._numeric_bounds_dirty_cols: set[str] | None = None  # None = need full rebuild

    def set_headers(self, headers: list[str]) -> None:
        self.beginResetModel()
        self._headers = list(headers)
        self._pixmap_columns &= set(self._headers)
        self._extra_pixmaps = {k: v for k, v in self._extra_pixmaps.items() if k[1] in self._headers}
        self._invalidate_numeric_bounds_all()
        self.endResetModel()

    def clear_rows(self) -> None:
        """Drop all rows and cached structure pixmaps; keep column headers."""
        self.beginResetModel()
        self._rows.clear()
        self._pixmaps.clear()
        self._oid_to_row.clear()
        self._extra_pixmaps.clear()
        self._invalidate_numeric_bounds_all()
        self.endResetModel()

    def clear(self) -> None:
        """Remove rows and clear the header list (empty table)."""
        self.beginResetModel()
        self._rows.clear()
        self._pixmaps.clear()
        self._headers.clear()
        self._oid_to_row.clear()
        self._pixmap_columns.clear()
        self._extra_pixmaps.clear()
        self._invalidate_numeric_bounds_all()
        self.endResetModel()

    def append_row(self, oid: int, cells: dict[str, str]) -> None:
        r = len(self._rows)
        self.beginInsertRows(QModelIndex(), r, r)
        self._rows.append(_Row(oid=oid, values=dict(cells)))
        self.endInsertRows()
        self._oid_to_row[oid] = len(self._rows) - 1
        bh = self._bounds_data_headers()
        for k in cells:
            if k in bh:
                self._mark_numeric_bounds_dirty({k})

    def insert_row_at(self, logical_index: int, oid: int, cells: dict[str, str]) -> None:
        n = len(self._rows)
        logical_index = max(0, min(logical_index, n))
        self.beginInsertRows(QModelIndex(), logical_index, logical_index)
        self._rows.insert(logical_index, _Row(oid=oid, values=dict(cells)))
        self.endInsertRows()
        self._rebuild_oid_index()
        self._invalidate_numeric_bounds_all()

    def remove_row_at(self, logical_row: int) -> None:
        if logical_row < 0 or logical_row >= len(self._rows):
            return
        self.beginRemoveRows(QModelIndex(), logical_row, logical_row)
        oid = self._rows[logical_row].oid
        self._rows.pop(logical_row)
        self._pixmaps.pop(oid, None)
        for k in [x for x in self._extra_pixmaps if x[0] == oid]:
            del self._extra_pixmaps[k]
        self.endRemoveRows()
        self._rebuild_oid_index()
        self._invalidate_numeric_bounds_all()

    def row_oid(self, logical_row: int) -> int:
        return self._rows[logical_row].oid

    def _rebuild_oid_index(self) -> None:
        self._oid_to_row = {row.oid: i for i, row in enumerate(self._rows)}

    def logical_row_for_oid(self, oid: int) -> int:
        r = self._oid_to_row.get(oid, -1)
        if 0 <= r < len(self._rows) and self._rows[r].oid == oid:
            return r
        self._rebuild_oid_index()
        return self._oid_to_row.get(oid, -1)

    def set_cell_text(self, oid: int, column_name: str, text: str) -> None:
        if column_name in ("ID_HIDDEN", "Structure") or column_name in self._pixmap_columns:
            return
        r = self.logical_row_for_oid(oid)
        if r < 0:
            return
        self._rows[r].values[column_name] = text
        if column_name in self._bounds_data_headers():
            self._mark_numeric_bounds_dirty({column_name})
        try:
            c = self._headers.index(column_name)
        except ValueError:
            return
        idx = self.index(r, c)
        self.dataChanged.emit(idx, idx, [Qt.DisplayRole, Qt.EditRole])

    def set_cell_text_batch(self, oid: int, values: dict[str, str]) -> None:
        """Set several text cells on one row; emit ``dataChanged`` once for the affected column span."""
        if not values:
            return
        r = self.logical_row_for_oid(oid)
        if r < 0:
            return
        changed_cols: list[int] = []
        for column_name, text in values.items():
            if column_name in ("ID_HIDDEN", "Structure") or column_name in self._pixmap_columns:
                continue
            try:
                c = self._headers.index(column_name)
            except ValueError:
                continue
            self._rows[r].values[column_name] = str(text)
            changed_cols.append(c)
        if not changed_cols:
            return
        dirty = {self._headers[c] for c in changed_cols if self._headers[c] in self._bounds_data_headers()}
        if dirty:
            self._mark_numeric_bounds_dirty(dirty)
        lo, hi = min(changed_cols), max(changed_cols)
        idx_tl = self.index(r, lo)
        idx_br = self.index(r, hi)
        self.dataChanged.emit(idx_tl, idx_br, [Qt.DisplayRole, Qt.EditRole])

    def set_structure_pixmap(self, oid: int, pixmap: QPixmap | None) -> None:
        if pixmap is not None:
            self._pixmaps[oid] = pixmap
        else:
            self._pixmaps.pop(oid, None)
        r = self.logical_row_for_oid(oid)
        if r < 0:
            return
        idx = self.index(r, self.STRUCTURE_COL)
        self.dataChanged.emit(idx, idx, [Qt.DecorationRole, Qt.SizeHintRole, Qt.DisplayRole, Qt.ToolTipRole])

    def register_pixmap_column(self, header_name: str) -> None:
        """Mark a data column as image-only (2D pixmap via ``set_column_pixmap``)."""
        if header_name in self._headers:
            self._pixmap_columns.add(header_name)

    def column_accepts_text_edit(self, logical_col: int) -> bool:
        """Plain string cells: not id, not structure, not pixmap-only columns."""
        if logical_col < 2 or logical_col >= len(self._headers):
            return False
        return self._headers[logical_col] not in self._pixmap_columns

    def set_column_pixmap(self, oid: int, header_name: str, pixmap: QPixmap | None) -> None:
        if header_name not in self._pixmap_columns:
            return
        key = (oid, header_name)
        if pixmap is not None:
            self._extra_pixmaps[key] = pixmap
        else:
            self._extra_pixmaps.pop(key, None)
        r = self.logical_row_for_oid(oid)
        if r < 0:
            return
        try:
            c = self._headers.index(header_name)
        except ValueError:
            return
        idx = self.index(r, c)
        self.dataChanged.emit(idx, idx, [Qt.DecorationRole, Qt.SizeHintRole])

    def snapshot_structure_pixmaps(self, oids: list[int]) -> dict[int, QPixmap | None]:
        """Shallow copies of structure pixmaps for undo/cancel (caller owns QPixmap copies)."""
        out: dict[int, QPixmap | None] = {}
        for oid in oids:
            p = self._pixmaps.get(oid)
            if p is not None and not p.isNull():
                out[oid] = QPixmap(p)
            else:
                out[oid] = None
        return out

    def snapshot_column_pixmaps(self, header_name: str, oids: list[int]) -> dict[int, QPixmap | None]:
        """Shallow copies of pixmap-column images for a header (e.g. Render 2D target column)."""
        out: dict[int, QPixmap | None] = {}
        if header_name not in self._headers:
            return out
        for oid in oids:
            key = (oid, header_name)
            p = self._extra_pixmaps.get(key)
            if p is not None and not p.isNull():
                out[oid] = QPixmap(p)
            else:
                out[oid] = None
        return out

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._headers)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row < 0 or row >= len(self._rows) or col < 0 or col >= len(self._headers):
            return None
        h = self._headers[col]
        oid = self._rows[row].oid

        if col == 0:
            if role in (Qt.DisplayRole, Qt.EditRole):
                return str(oid)
            return None

        if col == self.STRUCTURE_COL:
            if role == Qt.DecorationRole:
                pix = self._pixmaps.get(oid)
                if pix is not None and not pix.isNull():
                    return pix
                return None
            if role in (Qt.DisplayRole, Qt.EditRole):
                pix = self._pixmaps.get(oid)
                if pix is not None and not pix.isNull():
                    return ""
                return STRUCTURE_COLUMN_PENDING_HINT
            if role == Qt.TextAlignmentRole:
                pix = self._pixmaps.get(oid)
                if pix is not None and not pix.isNull():
                    return int(Qt.AlignCenter)
                return int(Qt.AlignCenter | Qt.AlignVCenter)
            if role == Qt.SizeHintRole:
                pix = self._pixmaps.get(oid)
                if pix is not None and not pix.isNull():
                    return QSize(pix.width(), pix.height())
                return QSize(STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT)
            if role == Qt.ToolTipRole:
                pix = self._pixmaps.get(oid)
                if pix is not None and not pix.isNull():
                    return None
                return STRUCTURE_COLUMN_PENDING_HINT
            return None

        if h in self._pixmap_columns:
            if role == Qt.DecorationRole:
                return self._extra_pixmaps.get((oid, h))
            if role == Qt.DisplayRole:
                return ""
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignCenter)
            if role == Qt.SizeHintRole:
                pix = self._extra_pixmaps.get((oid, h))
                if pix is not None and not pix.isNull():
                    return QSize(pix.width(), pix.height())
                return QSize(STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT)
            return None

        if role in (Qt.DisplayRole, Qt.EditRole):
            return self._rows[row].values.get(h, "")
        if role == Qt.TextAlignmentRole:
            v = self._rows[row].values.get(h, "")
            if safe_float(v) is not None:
                return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:  # noqa: N802
        if not index.isValid() or role != Qt.EditRole:
            return False
        row, col = index.row(), index.column()
        if row < 0 or row >= len(self._rows) or col < 2:
            return False
        h = self._headers[col]
        self._rows[row].values[h] = str(value)
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:  # noqa: N802
        if not index.isValid():
            return Qt.NoItemFlags
        col = index.column()
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if col >= 2:
            h = self._headers[col]
            if h not in self._pixmap_columns:
                base |= Qt.ItemIsEditable
        return base

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self._headers):
            return self._headers[section]
        if orientation == Qt.Vertical:
            return str(section + 1)
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder, *, sort_kind: str = "auto") -> None:  # noqa: N802
        """Sort rows by *column*. *sort_kind*: ``auto`` (numbers then text), ``numeric``, or ``alphabetic``."""
        if column < 0 or column >= len(self._headers):
            return
        h = self._headers[column]
        rev = order == Qt.DescendingOrder

        def key_auto(row: _Row) -> tuple:
            if column == 0:
                return (0, row.oid)
            if column == 1:
                return (0, row.oid)
            raw = row.values.get(h, "") or ""
            f = safe_float(raw)
            if f is not None:
                return (0, float(f))
            return (1, raw.lower())

        def key_numeric(row: _Row) -> tuple:
            if column == 0:
                return (0, row.oid)
            if column == 1:
                return (0, row.oid)
            raw = (row.values.get(h, "") or "").strip()
            f = safe_float(raw)
            if f is not None:
                return (0, float(f))
            return (1, raw.lower())

        def key_alpha(row: _Row) -> tuple:
            if column == 0:
                return (0, str(row.oid))
            if column == 1:
                return (0, str(row.oid))
            raw = (row.values.get(h, "") or "").strip()
            return (1, raw.lower())

        if sort_kind == "numeric":
            key = key_numeric
        elif sort_kind == "alphabetic":
            key = key_alpha
        else:
            key = key_auto

        self.layoutAboutToBeChanged.emit([], QAbstractItemModel.VerticalSortHint)
        self._rows.sort(key=key, reverse=rev)
        self.layoutChanged.emit([], QAbstractItemModel.VerticalSortHint)
        self._rebuild_oid_index()

    def all_oids_in_order(self) -> list[int]:
        return [r.oid for r in self._rows]

    def cell_text(self, row: int, col: int) -> str:
        if row < 0 or row >= len(self._rows) or col < 0 or col >= len(self._headers):
            return ""
        if col == 0:
            return str(self._rows[row].oid)
        if col == self.STRUCTURE_COL:
            oid = self._rows[row].oid
            pix = self._pixmaps.get(oid)
            if pix is not None and not pix.isNull():
                return ""
            return STRUCTURE_COLUMN_PENDING_HINT
        h = self._headers[col]
        if h in self._pixmap_columns:
            return ""
        return self._rows[row].values.get(h, "") or ""

    def value_for_header(self, row: int, header_name: str) -> str:
        """Raw string cell for a data column (no QModelIndex); empty if unknown column."""
        if row < 0 or row >= len(self._rows):
            return ""
        if header_name in self._pixmap_columns:
            return ""
        return self._rows[row].values.get(header_name, "") or ""

    def backing_value_for_row_header(self, row: int, header_name: str) -> str:
        """Stored cell text even when ``header_name`` is a pixmap-only column (hidden from ``cell_text``)."""
        if row < 0 or row >= len(self._rows):
            return ""
        return (self._rows[row].values.get(header_name, "") or "").strip()

    def structure_pixmap_copy(self, oid: int) -> QPixmap | None:
        """Detached copy of the main structure pixmap for this molecule id, if any."""
        pm = self._pixmaps.get(oid)
        if pm is not None and not pm.isNull():
            return QPixmap(pm)
        return None

    def extra_column_pixmaps_copy(self, oid: int) -> dict[str, QPixmap]:
        """Detached copies of extra pixmap-column images for this oid."""
        out: dict[str, QPixmap] = {}
        for (o, h), pm in list(self._extra_pixmaps.items()):
            if o == oid and pm is not None and not pm.isNull():
                out[h] = QPixmap(pm)
        return out

    def is_pixmap_data_column(self, header_name: str) -> bool:
        return header_name in self._pixmap_columns

    def column_pixmap_copy(self, oid: int, header_name: str) -> QPixmap | None:
        pm = self._extra_pixmaps.get((oid, header_name))
        if pm is not None and not pm.isNull():
            return QPixmap(pm)
        return None

    def _bounds_data_headers(self) -> frozenset[str]:
        return frozenset(
            h
            for h in self._headers[2:]
            if h not in ("ID_HIDDEN", "Structure")
            and h not in self._pixmap_columns
            and h not in _NON_NUMERIC_BLOB_COLUMNS
        )

    def _invalidate_numeric_bounds_all(self) -> None:
        self._numeric_bounds_cache = None
        self._numeric_bounds_key = None
        self._numeric_bounds_dirty_cols = None

    def _mark_numeric_bounds_dirty(self, columns: set[str]) -> None:
        if not columns:
            return
        if self._numeric_bounds_cache is None:
            return
        if self._numeric_bounds_dirty_cols is None:
            return
        self._numeric_bounds_dirty_cols |= columns

    @staticmethod
    def _scan_numeric_column(rows: list[_Row], h: str) -> dict | None:
        lo = hi = None
        int_ok = True
        for row in rows:
            f = safe_float(row.values.get(h, ""))
            if f is None:
                continue
            fv = float(f)
            if lo is None:
                lo = hi = fv
                int_ok = f.is_integer()
            else:
                if fv < lo:
                    lo = fv
                if fv > hi:
                    hi = fv
                if not f.is_integer():
                    int_ok = False
        if lo is None:
            return None
        return {"min": lo, "max": hi, "is_int": int_ok}

    def _full_numeric_bounds_scan(self) -> dict[str, dict]:
        data_headers = sorted(
            h
            for h in self._headers[2:]
            if h not in ("ID_HIDDEN", "Structure")
            and h not in self._pixmap_columns
            and h not in _NON_NUMERIC_BLOB_COLUMNS
        )
        if not data_headers:
            return {}
        out: dict[str, dict] = {}
        for h in data_headers:
            meta = self._scan_numeric_column(self._rows, h)
            if meta is not None:
                out[h] = meta
        return out

    def numeric_bounds_by_column(self) -> dict[str, dict]:
        """Numeric min/max per data column for filter sliders.

        Maintains a cache and only rescans columns touched since the last call when possible.
        """
        data_headers = sorted(
            h
            for h in self._headers[2:]
            if h not in ("ID_HIDDEN", "Structure")
            and h not in self._pixmap_columns
            and h not in _NON_NUMERIC_BLOB_COLUMNS
        )
        key = tuple(data_headers)
        if not data_headers:
            self._invalidate_numeric_bounds_all()
            return {}

        if self._numeric_bounds_cache is None or self._numeric_bounds_key != key:
            self._numeric_bounds_cache = self._full_numeric_bounds_scan()
            self._numeric_bounds_key = key
            self._numeric_bounds_dirty_cols = set()
            return dict(self._numeric_bounds_cache)

        if not self._numeric_bounds_dirty_cols:
            return dict(self._numeric_bounds_cache)

        cache = self._numeric_bounds_cache
        for h in list(self._numeric_bounds_dirty_cols):
            if h not in key:
                continue
            meta = self._scan_numeric_column(self._rows, h)
            if meta is None:
                cache.pop(h, None)
            else:
                cache[h] = meta
        self._numeric_bounds_dirty_cols = set()
        return dict(cache)

    def set_cell_text_at(self, row: int, col: int, text: str) -> None:
        if row < 0 or row >= len(self._rows) or col < 2 or col >= len(self._headers):
            return
        h = self._headers[col]
        if h in self._pixmap_columns:
            return
        self._rows[row].values[h] = str(text)
        if h in self._bounds_data_headers():
            self._mark_numeric_bounds_dirty({h})
        idx = self.index(row, col)
        self.dataChanged.emit(idx, idx, [Qt.DisplayRole, Qt.EditRole])

    def set_column_text_by_oids(self, column_name: str, oid_values: list[tuple[int, str]]) -> None:
        """Set one text column for many molecule ids; batch ``dataChanged`` (contiguous row runs)."""
        if not oid_values or column_name in ("ID_HIDDEN", "Structure") or column_name in self._pixmap_columns:
            return
        try:
            col = self._headers.index(column_name)
        except ValueError:
            return
        rows_changed: list[int] = []
        for oid, text in oid_values:
            r = self.logical_row_for_oid(oid)
            if r < 0:
                continue
            self._rows[r].values[column_name] = str(text)
            rows_changed.append(r)
        if not rows_changed:
            return
        if column_name in self._bounds_data_headers():
            self._mark_numeric_bounds_dirty({column_name})
        rows_changed.sort()
        i = 0
        while i < len(rows_changed):
            lo_r = hi_r = rows_changed[i]
            while i + 1 < len(rows_changed) and rows_changed[i + 1] == hi_r + 1:
                i += 1
                hi_r = rows_changed[i]
            self.dataChanged.emit(
                self.index(lo_r, col), self.index(hi_r, col), [Qt.DisplayRole, Qt.EditRole]
            )
            i += 1

    def insert_column_at(self, col: int, header_name: str, copy_from_logical: int | None = None) -> None:
        n = len(self._headers)
        if col < 0 or col > n:
            return
        copy_key = None
        if copy_from_logical is not None and 0 <= copy_from_logical < n:
            copy_key = self._headers[copy_from_logical]
        self.beginInsertColumns(QModelIndex(), col, col)
        self._headers.insert(col, header_name)
        if copy_key is not None:
            for row in self._rows:
                row.values[header_name] = row.values.get(copy_key, "")
        self._invalidate_numeric_bounds_all()
        self.endInsertColumns()

    def remove_column_at(self, col: int) -> None:
        if col < 0 or col >= len(self._headers):
            return
        h = self._headers[col]
        if h in self._pixmap_columns:
            self._pixmap_columns.discard(h)
            for k in [x for x in self._extra_pixmaps if x[1] == h]:
                del self._extra_pixmaps[k]
        self.beginRemoveColumns(QModelIndex(), col, col)
        self._headers.pop(col)
        if col >= 2:
            for row in self._rows:
                row.values.pop(h, None)
        self._invalidate_numeric_bounds_all()
        self.endRemoveColumns()

    def rename_header_at(self, col: int, new_name: str) -> None:
        if col < 0 or col >= len(self._headers) or self._headers[col] == new_name:
            return
        old = self._headers[col]
        self._headers[col] = new_name
        if col >= 2 and old in self._pixmap_columns:
            self._pixmap_columns.discard(old)
            self._pixmap_columns.add(new_name)
            for row in self._rows:
                oid = row.oid
                kk = (oid, old)
                if kk in self._extra_pixmaps:
                    self._extra_pixmaps[(oid, new_name)] = self._extra_pixmaps.pop(kk)
        if col >= 2:
            for row in self._rows:
                if old in row.values:
                    row.values[new_name] = row.values.pop(old)
        self.headerDataChanged.emit(Qt.Horizontal, col, col)
        c0 = self.index(0, col)
        c1 = self.index(max(len(self._rows) - 1, 0), col)
        self.dataChanged.emit(c0, c1, [Qt.DisplayRole, Qt.EditRole])
        self._invalidate_numeric_bounds_all()

    def moveRow(self, sourceParent: QModelIndex, sourceRow: int, destinationParent: QModelIndex, destinationChild: int) -> bool:  # noqa: N802
        if sourceParent.isValid() or destinationParent.isValid():
            return False
        n = len(self._rows)
        if sourceRow < 0 or sourceRow >= n or destinationChild < 0 or destinationChild > n:
            return False
        if sourceRow == destinationChild:
            return True
        self.beginMoveRows(QModelIndex(), sourceRow, sourceRow, QModelIndex(), destinationChild)
        row = self._rows.pop(sourceRow)
        if destinationChild > sourceRow:
            self._rows.insert(destinationChild, row)
        else:
            self._rows.insert(destinationChild, row)
        self.endMoveRows()
        self._rebuild_oid_index()
        return True


class StructureDelegate(QStyledItemDelegate):
    """Paints cached structure pixmap or a neutral placeholder."""

    def paint(self, painter, option, index):  # noqa: N802
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        painter.fillRect(opt.rect, QColor(255, 255, 255))
        pix = index.data(Qt.DecorationRole)
        if isinstance(pix, QPixmap) and not pix.isNull():
            r = opt.rect
            x = r.x() + (r.width() - pix.width()) // 2
            y = r.y() + (r.height() - pix.height()) // 2
            painter.drawPixmap(x, y, pix)
        else:
            hint = index.data(Qt.DisplayRole)
            text = hint if isinstance(hint, str) and hint.strip() else "—"
            painter.save()
            painter.setFont(opt.font)
            painter.setPen(opt.palette.text().color())
            margin = 6
            r = QRect(
                opt.rect.x() + margin,
                opt.rect.y() + margin,
                max(1, opt.rect.width() - 2 * margin),
                max(1, opt.rect.height() - 2 * margin),
            )
            painter.drawText(r, int(Qt.AlignCenter | Qt.TextWordWrap), text)
            painter.restore()

    def sizeHint(self, option, index):  # noqa: N802
        sh = super().sizeHint(option, index)
        pix = index.data(Qt.DecorationRole)
        if isinstance(pix, QPixmap) and not pix.isNull():
            return QSize(max(sh.width(), pix.width() + 8), max(sh.height(), pix.height() + 8))
        return QSize(STRUCTURE_DEPICT_WIDTH, max(sh.height(), STRUCTURE_DEPICT_HEIGHT))


class CompoundTableView(QTableView):
    """
    QTableView pre-wired for ``CompoundTableModel`` + structure delegate.
    Call ``set_compound_model`` after construction.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectItems)
        self.setSelectionMode(QTableView.ExtendedSelection)
        self.verticalHeader().setDefaultSectionSize(STRUCTURE_ROW_DEFAULT_HEIGHT)
        self.verticalHeader().setSectionsMovable(True)
        hh = self.horizontalHeader()
        hh.setSectionsMovable(True)
        hh.setFirstSectionMovable(False)
        hh.setSectionResizeMode(QHeaderView.Interactive)
        self.setSortingEnabled(False)
        self._compound_model: CompoundTableModel | None = None

    def set_compound_model(self, model: CompoundTableModel) -> None:
        self._compound_model = model
        self.setModel(model)
        self.setItemDelegateForColumn(CompoundTableModel.STRUCTURE_COL, StructureDelegate(self))
        if model.columnCount() > 0:
            self.setColumnHidden(0, True)

    def compound_model(self) -> CompoundTableModel | None:
        return self._compound_model


def run_table_model_demo() -> int:
    """Small window demonstrating the model + view + sort + pixmap update."""
    from PyQt5.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget, QPushButton

    app = QApplication.instance() or QApplication([])

    headers = ["ID_HIDDEN", "Structure", "SMILES", "MW"]
    model = CompoundTableModel(headers)
    for i in range(8):
        smi = "C" * (i + 1)
        model.append_row(oid=100 + i, cells={"SMILES": smi, "MW": str(50 + i * 13)})

    w = QMainWindow()
    w.setWindowTitle("ChemManager — QAbstractTableModel prototype")
    cw = QWidget()
    ly = QVBoxLayout(cw)
    view = CompoundTableView()
    view.set_compound_model(model)
    view.resizeColumnsToContents()
    ly.addWidget(
        QLabel(
            "Click a column header to select that column; right-click the header for Sort (numeric or alphabetic). "
            "Structure column shows placeholders until you click the button."
        )
    )
    ly.addWidget(view)

    def fake_render():
        from PyQt5.QtGui import QColor, QPainter

        for oid in model.all_oids_in_order():
            pm = QPixmap(120, 90)
            pm.fill(QColor(240, 248, 255))
            p = QPainter(pm)
            p.setPen(Qt.darkGray)
            p.drawText(pm.rect(), Qt.AlignCenter, f"id {oid}")
            p.end()
            model.set_structure_pixmap(oid, pm)

    btn = QPushButton("Simulate 2D render (placeholder pixmap per row)")
    btn.clicked.connect(fake_render)
    ly.addWidget(btn)
    w.setCentralWidget(cw)
    w.resize(900, 520)
    w.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(run_table_model_demo())
