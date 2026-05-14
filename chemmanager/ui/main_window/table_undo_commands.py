"""QUndoCommand implementations for table row/column/cell edits."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QUndoCommand
from rdkit import Chem

from ..compound_table_model import CompoundTableModel
from ..widgets import CategoryFilterCard, FilterCard, TextFilterCard

if TYPE_CHECKING:
    from .table_ui_mixin import TableUIMixin

__all__ = [
    "UndoDeleteRowsCommand",
    "UndoPasteCellCommand",
    "UndoCellTextChangeCommand",
    "UndoDeleteColumnCommand",
    "UndoDuplicateColumnCommand",
    "UndoInsertRowCommand",
]


class UndoDeleteRowsCommand(QUndoCommand):
    """Undo/redo for removing one or more table rows (model + mols + structure pixmaps)."""

    def __init__(self, app: TableUIMixin, rows: list[int]) -> None:
        n = len(set(rows))
        super().__init__(f"Delete {n} row(s)" if n != 1 else "Delete row")
        self._app = app
        self._snapshots: list[
            tuple[int, int, dict[str, str], Chem.Mol | None, QPixmap | None, dict[str, QPixmap]]
        ] = []
        for r in sorted(set(rows)):
            t0 = app._table_model.cell_text(r, 0)
            if not t0.isdigit():
                continue
            oid = int(t0)
            cells: dict[str, str] = {}
            for h in app.headers[2:]:
                if h == "Structure":
                    continue
                cells[h] = app._table_model.value_for_header(r, h)
            pm = app.mols.get(oid)
            mol_copy = Chem.Mol(pm) if pm is not None else None
            spm = app._table_model.structure_pixmap_copy(oid)
            extra = app._table_model.extra_column_pixmaps_copy(oid)
            self._snapshots.append((r, oid, cells, mol_copy, spm, extra))

    def snapshot_count(self) -> int:
        return len(self._snapshots)

    def redo(self) -> None:
        app = self._app
        app.table.setSortingEnabled(False)
        oids = [s[1] for s in self._snapshots]
        for oid in sorted(oids, key=lambda o: -app._table_model.logical_row_for_oid(o)):
            r = app._table_model.logical_row_for_oid(oid)
            if r >= 0:
                app._table_model.remove_row_at(r)
                app.mols.pop(oid, None)
                app.zoomed_ids.discard(oid)
        app._confs_sidecar_discard_oids(oids)
        app.calculate_global_bounds()
        app.apply_filters()
        app.table.setSortingEnabled(False)
        app.status_label.setText(f"Deleted {len(self._snapshots)} row(s).")

    def undo(self) -> None:
        app = self._app
        app.table.setSortingEnabled(False)
        for k, snap in enumerate(sorted(self._snapshots, key=lambda s: s[0])):
            orig_row, oid, cells, mol_copy, spm, extra = snap
            insert_at = orig_row + k
            app._table_model.insert_row_at(insert_at, oid, dict(cells))
            if mol_copy is not None:
                app.mols[oid] = Chem.Mol(mol_copy)
            else:
                app.mols.pop(oid, None)
            if spm is not None:
                app._table_model.set_structure_pixmap(oid, spm)
            else:
                app._table_model.set_structure_pixmap(oid, None)
            for h, pm in extra.items():
                app._table_model.set_column_pixmap(oid, h, pm)
        app.calculate_global_bounds()
        app.apply_filters()
        app.table.setSortingEnabled(False)
        app.status_label.setText(f"Undo: restored {len(self._snapshots)} row(s).")


class UndoPasteCellCommand(QUndoCommand):
    """Undo/redo for pasting into one table cell (text or structure column)."""

    def __init__(self, app: TableUIMixin, row: int, col: int, oid: int, clip_text: str) -> None:
        super().__init__("Paste")
        self._app = app
        self._row = row
        self._col = col
        self._oid = oid
        self._clip = clip_text
        if col == CompoundTableModel.STRUCTURE_COL:
            pm = app.mols.get(oid)
            self._prev_mol = Chem.Mol(pm) if pm is not None else None
            self._prev_pm = app._table_model.structure_pixmap_copy(oid)
            if "SMILES" in app.headers:
                self._prev_smiles = app._table_model.value_for_header(row, "SMILES")
            else:
                self._prev_smiles = ""
            self._prev_text = ""
        else:
            h = app.headers[col]
            self._prev_text = app._table_model.value_for_header(row, h)
            self._prev_mol = None
            self._prev_pm = None
            self._prev_smiles = ""

    def redo(self) -> None:
        self._app._paste_clipboard_into_table_cell(
            self._row, self._col, self._oid, clip_text=self._clip, quiet=True
        )

    def undo(self) -> None:
        app = self._app
        oid = self._oid
        if self._col == CompoundTableModel.STRUCTURE_COL:
            if self._prev_mol is not None:
                app.mols[oid] = Chem.Mol(self._prev_mol)
            else:
                app.mols.pop(oid, None)
            if "SMILES" in app.headers:
                app._table_model.set_cell_text(oid, "SMILES", self._prev_smiles or "")
            app._table_model.set_structure_pixmap(oid, self._prev_pm)
        else:
            h = app.headers[self._col]
            app._table_model.set_cell_text(oid, h, self._prev_text)
        app.calculate_global_bounds()
        app.apply_filters()
        app.status_label.setText("Undo: paste reverted.")


class UndoCellTextChangeCommand(QUndoCommand):
    """Undo/redo for context-menu Edit Value or Clear Value on a text data cell."""

    def __init__(self, app: TableUIMixin, oid: int, header: str, old_text: str, new_text: str) -> None:
        label = "Clear cell" if new_text == "" else "Edit cell"
        super().__init__(label)
        self._app = app
        self._oid = oid
        self._header = header
        self._old = old_text
        self._new = new_text

    def redo(self) -> None:
        app = self._app
        app._table_model.set_cell_text(self._oid, self._header, self._new)
        app.calculate_global_bounds()
        app.apply_filters()

    def undo(self) -> None:
        app = self._app
        app._table_model.set_cell_text(self._oid, self._header, self._old)
        app.calculate_global_bounds()
        app.apply_filters()
        app.status_label.setText("Undo: cell value reverted.")


class UndoDeleteColumnCommand(QUndoCommand):
    """Undo/redo deleting a data column (not ID or Structure)."""

    def __init__(self, app: TableUIMixin, col: int) -> None:
        hdr = app.headers[col]
        super().__init__(f"Delete column '{hdr}'")
        self._app = app
        self._hdr = hdr
        self._logical_col = col
        self._was_pixmap = app._table_model.is_pixmap_data_column(hdr)
        self._text_by_oid: dict[int, str] = {}
        self._pixmap_by_oid: dict[int, QPixmap] = {}
        for r in range(app._table_model.rowCount()):
            t0 = app._table_model.cell_text(r, 0)
            if not t0.isdigit():
                continue
            oid = int(t0)
            if self._was_pixmap:
                pm = app._table_model.column_pixmap_copy(oid, hdr)
                if pm is not None:
                    self._pixmap_by_oid[oid] = pm
            else:
                self._text_by_oid[oid] = app._table_model.value_for_header(r, hdr)

    def redo(self) -> None:
        app = self._app
        try:
            idx = app.headers.index(self._hdr)
        except ValueError:
            return
        app._session_sort = None
        app._table_model.remove_column_at(idx)
        app.headers.pop(idx)
        app.global_bounds.pop(self._hdr, None)
        cols = app._filterable_data_column_names()
        to_rem = []
        for f in app.filters:
            if isinstance(f, FilterCard):
                if f.update_prop_list(list(app.global_bounds.keys()), self._hdr, None):
                    to_rem.append(f)
            elif isinstance(f, (TextFilterCard, CategoryFilterCard)):
                if f.update_prop_list(cols, self._hdr, None):
                    to_rem.append(f)
        for f in to_rem:
            app.remove_filter(f)
        app.calculate_global_bounds()
        app.apply_filters()
        app.status_label.setText(f"Deleted column '{self._hdr}'.")

    def undo(self) -> None:
        app = self._app
        idx = min(self._logical_col, len(app.headers))
        app.headers.insert(idx, self._hdr)
        app._table_model.insert_column_at(idx, self._hdr, copy_from_logical=None)
        if self._was_pixmap:
            app._table_model.register_pixmap_column(self._hdr)
            for oid, pm in self._pixmap_by_oid.items():
                app._table_model.set_column_pixmap(oid, self._hdr, pm)
        else:
            for oid, txt in self._text_by_oid.items():
                app._table_model.set_cell_text(oid, self._hdr, txt)
        app.calculate_global_bounds()
        app.apply_filters()
        app.status_label.setText(f"Undo: restored column '{self._hdr}'.")


class UndoDuplicateColumnCommand(QUndoCommand):
    """Undo/redo duplicating a column (insert copy next to source)."""

    def __init__(self, app: TableUIMixin, src_col: int, src_name: str) -> None:
        super().__init__(f"Duplicate column '{src_name}'")
        self._app = app
        self._src_name = src_name
        self._dup_name = f"{src_name} (Copy)"

    def redo(self) -> None:
        app = self._app
        try:
            src = app.headers.index(self._src_name)
        except ValueError:
            return
        app.table.setSortingEnabled(False)
        dup_col = src + 1
        app.headers.insert(dup_col, self._dup_name)
        app._table_model.insert_column_at(dup_col, self._dup_name, copy_from_logical=src)
        app.calculate_global_bounds()
        app.table.setSortingEnabled(False)
        app.status_label.setText(f"Duplicated column '{self._src_name}'.")

    def undo(self) -> None:
        app = self._app
        try:
            idx = app.headers.index(self._dup_name)
        except ValueError:
            return
        app._session_sort = None
        app._table_model.remove_column_at(idx)
        app.headers.pop(idx)
        app.global_bounds.pop(self._dup_name, None)
        cols = app._filterable_data_column_names()
        to_rem = []
        for f in app.filters:
            if isinstance(f, FilterCard):
                if f.update_prop_list(list(app.global_bounds.keys()), self._dup_name, None):
                    to_rem.append(f)
            elif isinstance(f, (TextFilterCard, CategoryFilterCard)):
                if f.update_prop_list(cols, self._dup_name, None):
                    to_rem.append(f)
        for f in to_rem:
            app.remove_filter(f)
        app.calculate_global_bounds()
        app.apply_filters()
        app.status_label.setText(f"Undo: removed duplicated column '{self._dup_name}'.")


class UndoInsertRowCommand(QUndoCommand):
    """Undo/redo inserting a duplicated row (same data as source row at action time)."""

    def __init__(self, app: TableUIMixin, src_row: int) -> None:
        super().__init__("Duplicate row")
        self._app = app
        self._src_oid = -1
        self._cells: dict[str, str] = {}
        self._mol_copy: Chem.Mol | None = None
        self._new_oid: int | None = None
        t0 = app._table_model.cell_text(src_row, 0)
        if not t0.isdigit():
            return
        self._src_oid = int(t0)
        self._cells = dict(app._row_cells_dict(src_row))
        pm = app.mols.get(self._src_oid)
        self._mol_copy = Chem.Mol(pm) if pm is not None else None

    def is_valid(self) -> bool:
        return self._src_oid >= 0

    def redo(self) -> None:
        if not self.is_valid():
            return
        app = self._app
        if self._new_oid is None:
            self._new_oid = app.next_oid
            app.next_oid += 1
        src_r = app._table_model.logical_row_for_oid(self._src_oid)
        if src_r < 0:
            return
        insert_at = src_r + 1
        app.table.setSortingEnabled(False)
        app._table_model.insert_row_at(insert_at, self._new_oid, dict(self._cells))
        if self._mol_copy is not None:
            app.mols[self._new_oid] = Chem.Mol(self._mol_copy)
            app.start_render_worker(self._new_oid, app.mols[self._new_oid])
        app._confs_sidecar_copy_for_new_row(self._src_oid, self._new_oid)
        app.calculate_global_bounds()
        app.apply_filters()
        app.table.setSortingEnabled(False)
        app.status_label.setText("Duplicated row.")

    def undo(self) -> None:
        if self._new_oid is None:
            return
        app = self._app
        app.table.setSortingEnabled(False)
        r = app._table_model.logical_row_for_oid(self._new_oid)
        if r >= 0:
            app._table_model.remove_row_at(r)
        app.mols.pop(self._new_oid, None)
        app.zoomed_ids.discard(self._new_oid)
        app._confs_sidecar_discard_oids([self._new_oid])
        app.calculate_global_bounds()
        app.apply_filters()
        app.table.setSortingEnabled(False)
        app.status_label.setText("Undo: removed duplicated row.")
