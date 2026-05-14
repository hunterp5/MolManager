from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time

from PyQt5.QtCore import QItemSelection, QItemSelectionModel, Qt
from PyQt5.QtGui import QImage, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from rdkit import Chem

from ...confs_codec import (
    demote_v1_cell_to_sidecar,
    resolve_blocks_b64_for_viewer,
    rehydrate_v1_confs_cell,
)
from ...utils import (
    looks_like_mol_block,
    mol_to_canonical_smiles,
    parse_molecule_from_cell_text,
    safe_mol_prop_string,
)
from ..compound_table_model import CompoundTableModel
from ..singleton_modeless_dialog import reuse_or_show_modeless_singleton
from ..strings import TOOL_RENDER_2D
from ..widgets import CategoryFilterCard, FilterCard, SubstructureFilterCard, TextFilterCard

from ..filters import FilterPanelMixin
from .table_search_mixin import TableSearchMixin
from .table_undo_commands import (
    UndoCellTextChangeCommand,
    UndoDeleteColumnCommand,
    UndoDeleteRowsCommand,
    UndoDuplicateColumnCommand,
    UndoInsertRowCommand,
    UndoPasteCellCommand,
)


class TableUIMixin(TableSearchMixin, FilterPanelMixin):
    def _apply_table_sort(self, logical_col: int, ascending: bool, sort_kind: str) -> None:
        """Apply sort on the model and record state for session save/restore."""
        order = Qt.AscendingOrder if ascending else Qt.DescendingOrder
        self.table.setSortingEnabled(False)
        self._table_model.sort(logical_col, order, sort_kind=sort_kind)
        self._session_sort = {"column": logical_col, "ascending": ascending, "mode": sort_kind}

    def _on_horizontal_header_section_clicked(self, logical_index: int) -> None:
        if logical_index < 0 or logical_index >= len(self.headers):
            return
        if self.headers[logical_index] == "ID_HIDDEN":
            return
        self.table.setCurrentIndex(self._table_model.index(0, logical_index))
        self._select_column(logical_index)

    def _select_column(self, col: int) -> None:
        n = self._table_model.rowCount()
        if n <= 0:
            return
        prev_behavior = self.table.selectionBehavior()
        self.table.setSelectionBehavior(QAbstractItemView.SelectColumns)
        top = self._table_model.index(0, col)
        bottom = self._table_model.index(n - 1, col)
        sel = QItemSelection(top, bottom)
        self.table.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Columns)
        self.table.setSelectionBehavior(prev_behavior)

    def _selected_logical_rows(self) -> list[int]:
        """Distinct model row indices from the current selection (any column)."""
        sm = self.table.selectionModel()
        if sm is None:
            return []
        return sorted({ix.row() for ix in sm.selectedIndexes() if ix.isValid() and ix.row() >= 0})

    def _selected_oids_set(self) -> set[int]:
        oids: set[int] = set()
        for r in self._selected_logical_rows():
            t0 = self._table_model.cell_text(r, 0)
            if t0.isdigit():
                oids.add(int(t0))
        return oids

    def _all_oids_in_table_order(self) -> list[int]:
        """Every row OID top-to-bottom (only rows with a numeric hidden id)."""
        out: list[int] = []
        for r in range(self._table_model.rowCount()):
            t0 = self._table_model.cell_text(r, 0)
            if t0.isdigit():
                out.append(int(t0))
        return out

    def _row_cells_dict(self, row: int) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in self.headers[2:]:
            c = self.headers.index(name)
            out[name] = self._table_cell_text(row, c)
        return out

    def _on_column_moved(self, logicalIndex: int, oldVisualIndex: int, newVisualIndex: int) -> None:
        # Keep `ID_HIDDEN` (col 0) and `Structure` (col 1) fixed at visual positions 0 and 1.
        # Qt will still allow drops around them; we snap them back after the move.
        h = self.table.horizontalHeader()
        if self._table_model.columnCount() < 2:
            return
        if h.visualIndex(0) != 0:
            h.moveSection(h.visualIndex(0), 0)
        if h.visualIndex(1) != 1:
            h.moveSection(h.visualIndex(1), 1)

    def _visual_logical_columns(self) -> list[int]:
        """Logical column indices sorted by current visual order."""
        h = self.table.horizontalHeader()
        n = self._table_model.columnCount()
        cols = list(range(n))
        cols.sort(key=h.visualIndex)
        return cols

    def _table_cell_text(self, row: int, col: int) -> str:
        """Best-effort text for exporting (structure column has no text when shown as pixmap)."""
        if col == 1:
            return ""
        return (self._table_model.cell_text(row, col) or "").strip()

    @staticmethod
    def _header_looks_structural(name: str) -> bool:
        if name in ("ID_HIDDEN", "Structure", "Fragments"):
            return False
        n = (name or "").lower().strip()
        if not n:
            return False
        if "inchikey" in n:
            return False
        if "smiles" in n:
            return True
        if "inchi" in n:
            return True
        if n == "smi" or n.endswith("_smi") or n.endswith(".smi"):
            return True
        if n == "structure":
            return True
        if "molblock" in n or "mol_block" in n or "molfile" in n or n.endswith(".mol"):
            return True
        if "v2000" in n or "v3000" in n or "ctab" in n:
            return True
        if "sdf" in n and ("block" in n or "record" in n or n == "sdf"):
            return True
        if "rxn" in n or "reaction" in n:
            return True
        if n == "pdb" or n.endswith("_pdb") or "pdb_block" in n:
            return True
        if "smarts" in n or "smirks" in n:
            return True
        if "csmiles" in n or "cxsmiles" in n or "canonical_smiles" in n or "isomeric_smiles" in n:
            return True
        if "mol_string" in n or n in ("mol", "rmol") or n.endswith("_mol"):
            return True
        return False

    def _skip_chemistry_tool_column_dropdown(self, h: str) -> bool:
        """Exclude non-molecular columns from chemistry-tool source dropdowns."""
        if h in ("ID_HIDDEN", "Structure"):
            return True
        nl = (h or "").lower()
        if nl == "pka":
            return True
        if nl == "cluster" or nl.startswith("cluster ("):
            return True
        if "inchikey" in nl and "smiles" not in nl and "inchi" not in nl and "mol" not in nl:
            return True
        return False

    def _column_has_parseable_molecule_sample(
        self,
        header_name: str,
        *,
        max_rows_scan: int = 500,
        max_nonempty_samples: int = 80,
    ) -> bool:
        """True if a sample of cells in this column parses as a molecule (SMILES, InChI, MolBlock, SMARTS, …)."""
        if header_name not in self.headers:
            return False
        tries = 0
        n = min(self._table_model.rowCount(), max_rows_scan)
        for r in range(n):
            raw = self._table_model.backing_value_for_row_header(r, header_name)
            if not raw:
                try:
                    ci = self.headers.index(header_name)
                except ValueError:
                    continue
                raw = (self._table_cell_text(r, ci) or "").strip()
            if not raw:
                continue
            tries += 1
            if tries > max_nonempty_samples:
                break
            if len(raw) > 20000 and not looks_like_mol_block(raw):
                continue
            if parse_molecule_from_cell_text(raw) is not None:
                return True
        return False

    def _data_headers_confirmed_for_chemistry_tools(self) -> list[str]:
        """
        Data columns suitable as chemistry-tool sources: structural-looking names,
        optional ``_structure_field_override``, or at least one parseable cell in a bounded scan.
        """
        out: list[str] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            if not name or name in seen:
                return
            if name not in self.headers or self._skip_chemistry_tool_column_dropdown(name):
                return
            seen.add(name)
            out.append(name)

        ov = getattr(self, "_structure_field_override", None)
        if isinstance(ov, str) and ov.strip():
            add(ov.strip())
        for h in self.headers[2:]:
            if self._skip_chemistry_tool_column_dropdown(h):
                continue
            if self._header_looks_structural(h):
                add(h)
                continue
            if self._column_has_parseable_molecule_sample(h):
                add(h)
        return out

    def _should_skip_chemical_scan_column(self, h: str) -> bool:
        if h in ("ID_HIDDEN", "Structure"):
            return True
        if self._table_model.is_pixmap_data_column(h):
            return True
        nl = (h or "").lower()
        if "inchikey" in nl:
            return True
        return False

    def _ordered_headers_for_molecule_lookup(self) -> list[str]:
        """Column names to probe for parseable chemistry (likely names first, then all other data columns)."""
        seen: set[str] = set()
        out: list[str] = []

        def add(name: str | None) -> None:
            if not name or name not in self.headers or self._should_skip_chemical_scan_column(name):
                return
            if name not in seen:
                seen.add(name)
                out.append(name)

        ov = getattr(self, "_structure_field_override", None)
        if isinstance(ov, str) and ov.strip():
            add(ov.strip())
        for h in self.headers[2:]:
            if self._should_skip_chemical_scan_column(h):
                continue
            lo = h.strip().lower()
            if lo == "smiles" or (("smiles" in lo) and ("inchikey" not in lo)):
                add(h)
        for h in self.headers[2:]:
            if self._header_looks_structural(h):
                add(h)
        for h in self.headers[2:]:
            if not self._should_skip_chemical_scan_column(h):
                add(h)
        return out

    def _canonical_smiles_header_for_updates(self) -> str | None:
        """Column to store canonical SMILES after chemistry tools (prefer ``SMILES``)."""
        if "SMILES" in self.headers:
            return "SMILES"
        for h in self.headers[2:]:
            lo = h.strip().lower()
            if lo == "smiles" or (("smiles" in lo) and ("inchikey" not in lo)):
                return h
        return None

    def _is_smiles_named_header(self, h: str) -> bool:
        lo = (h or "").strip().lower()
        return lo == "smiles" or (("smiles" in lo) and ("inchikey" not in lo))

    def _fill_row_data_columns_from_mol(self, row_idx: int, mol: Chem.Mol | None) -> None:
        """Populate data columns (from col 2 onward) from RDKit mol properties — same source as RenderWorker props."""
        if not self.headers or row_idx < 0 or row_idx >= self._table_model.rowCount():
            return
        oid = self._table_model.row_oid(row_idx)
        for _c, name in enumerate(self.headers[2:], start=2):
            if mol is None:
                txt = ""
            elif name == "SMILES":
                if mol.HasProp("SMILES"):
                    txt = (safe_mol_prop_string(mol, "SMILES") or "").strip()
                else:
                    try:
                        txt = mol_to_canonical_smiles(mol)
                    except Exception:
                        txt = ""
            else:
                txt = safe_mol_prop_string(mol, name)
            self._table_model.set_cell_text(oid, name, txt)

    def _mol_for_structure_row(self, row: int) -> Chem.Mol | None:
        """Best-effort RDKit mol: in-memory store, then any parseable chemistry in table columns."""
        if row < 0 or row >= self._table_model.rowCount():
            return None
        t0 = self._table_model.cell_text(row, 0)
        oid = int(t0) if t0.isdigit() else None
        if oid is not None:
            m = self.mols.get(oid)
            if m is not None:
                return self._apply_structure_field_override(m)
        ov = getattr(self, "_structure_field_override", None)
        ov_s = str(ov).strip() if isinstance(ov, str) else ""
        for h in self._ordered_headers_for_molecule_lookup():
            ci = self.headers.index(h)
            raw = (self._table_model.cell_text(row, ci) or "").strip()
            if not raw:
                continue
            priority = (ov_s and h == ov_s) or self._is_smiles_named_header(h) or self._header_looks_structural(h)
            if not priority and len(raw) > 20000 and not looks_like_mol_block(raw):
                continue
            m = self._mol_from_structure_text(raw)
            if m is not None:
                return self._apply_structure_field_override(m)
        return None

    def _mol_from_structure_text(self, raw: str) -> Chem.Mol | None:
        return parse_molecule_from_cell_text(raw)

    def _apply_structure_field_override(self, mol: Chem.Mol | None) -> Chem.Mol | None:
        field = getattr(self, "_structure_field_override", None)
        if not field or mol is None:
            return mol
        if not mol.HasProp(field):
            return mol
        raw = (safe_mol_prop_string(mol, field) or "").strip()
        nm = self._mol_from_structure_text(raw)
        return nm if nm is not None else mol

    def _on_external_db_dialog_destroyed(self):
        self._external_db_dialog = None

    def _on_pubchem_dialog_destroyed(self):
        self._pubchem_dialog = None

    def _on_chembl_dialog_destroyed(self):
        self._chembl_dialog = None

    def get_row_by_id(self, original_idx):
        return self._table_model.logical_row_for_oid(int(original_idx))

    def _resolve_structure_row_for_oid(self, oid: int) -> int:
        """Table row index for this molecule id (stable during a Render 2D batch)."""
        rb = getattr(self, "_render2d_row_by_oid", None)
        if rb and oid in rb:
            row = rb[oid]
            if 0 <= row < self._table_model.rowCount():
                t0 = self._table_model.cell_text(row, 0)
                if t0.isdigit() and int(t0) == oid:
                    return row
        return self.get_row_by_id(oid)
    def show_header_menu(self, pos):
        col = self.table.horizontalHeader().logicalIndexAt(pos)
        if col < 0 or col >= len(self.headers):
            return
        if self.headers[col] == "ID_HIDDEN":
            return

        old_n = self.headers[col]
        menu = QMenu(self)
        sel_act = menu.addAction(f"Select column '{old_n}'")
        sort_num_asc = sort_num_desc = sort_alpha_asc = sort_alpha_desc = None
        if self._table_model.rowCount() > 0:
            sort_top = menu.addMenu("Sort")
            num_m = sort_top.addMenu("Numeric")
            sort_num_asc = num_m.addAction("Ascending")
            sort_num_desc = num_m.addAction("Descending")
            alp_m = sort_top.addMenu("Alphabetic")
            sort_alpha_asc = alp_m.addAction("Ascending")
            sort_alpha_desc = alp_m.addAction("Descending")
        menu.addSeparator()
        search_act = menu.addAction("Search")
        menu.addSeparator()
        ren_act = dup_act = del_act = None
        if old_n != "Structure":
            ren_act = menu.addAction(f"Rename '{old_n}'")
            dup_act = menu.addAction(f"Duplicate '{old_n}'")
            del_act = menu.addAction(f"Delete '{old_n}'")
        action = menu.exec_(self.table.horizontalHeader().mapToGlobal(pos))
        if action == sel_act:
            self.table.setCurrentIndex(self._table_model.index(0, col))
            self._select_column(col)
        elif sort_num_asc is not None and action == sort_num_asc:
            self._apply_table_sort(col, True, "numeric")
        elif sort_num_desc is not None and action == sort_num_desc:
            self._apply_table_sort(col, False, "numeric")
        elif sort_alpha_asc is not None and action == sort_alpha_asc:
            self._apply_table_sort(col, True, "alphabetic")
        elif sort_alpha_desc is not None and action == sort_alpha_desc:
            self._apply_table_sort(col, False, "alphabetic")
        elif action == search_act:
            self.open_table_search_with_column(col)
        elif del_act is not None and action == del_act:
            self._undo_stack.push(UndoDeleteColumnCommand(self, col))
        elif ren_act is not None and action == ren_act:
            name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old_n)
            if ok and name:
                self.headers[col] = name
                self._table_model.rename_header_at(col, name)
                if old_n in self.global_bounds:
                    self.global_bounds[name] = self.global_bounds.pop(old_n)
                cols = self._filterable_data_column_names()
                for f in self.filters:
                    if isinstance(f, FilterCard):
                        f.update_prop_list(list(self.global_bounds.keys()), old_n, name)
                    elif isinstance(f, (TextFilterCard, CategoryFilterCard)):
                        f.update_prop_list(cols, old_n, name)
        elif dup_act is not None and action == dup_act:
            self._undo_stack.push(UndoDuplicateColumnCommand(self, col, old_n))

    def show_row_header_menu(self, pos):
        row = self.table.verticalHeader().logicalIndexAt(pos)
        if row < 0:
            return
        menu = QMenu(self)
        dup_act = menu.addAction("Duplicate Row")
        del_act = menu.addAction("Delete Row")
        action = menu.exec_(self.table.verticalHeader().mapToGlobal(pos))
        if action == dup_act:
            cmd = UndoInsertRowCommand(self, row)
            if cmd.is_valid():
                self._undo_stack.push(cmd)

        elif action == del_act:
            t0 = self._table_model.cell_text(row, 0)
            if t0.isdigit():
                self._confirm_and_push_delete_rows([row])

    def _copy_text_for_table_cell(self, row: int, col: int, oid: int | None) -> tuple[bool, str]:
        """Whether copy is meaningful, and the string to place on the clipboard."""
        if oid is None:
            return False, ""
        if col == 0:
            return True, str(oid)
        if col == CompoundTableModel.STRUCTURE_COL:
            if "SMILES" in self.headers:
                sci = self.headers.index("SMILES")
                t = (self._table_model.cell_text(row, sci) or "").strip()
                if t:
                    return True, t
            mol = self.mols.get(oid)
            if mol is not None:
                try:
                    return True, mol_to_canonical_smiles(mol)
                except Exception:
                    return False, ""
            return False, ""
        if self._table_model.column_accepts_text_edit(col):
            return True, self._table_model.cell_text(row, col) or ""
        return False, ""

    def edit_copy(self) -> None:
        """Copy the current selection to the clipboard (tab-separated columns, newline-separated rows)."""
        sm = self.table.selectionModel()
        indexes = list(sm.selectedIndexes()) if sm is not None else []
        if not indexes:
            ix = self.table.currentIndex()
            if ix.isValid():
                indexes = [ix]
        if not indexes:
            self.status_label.setText("Copy: nothing selected.")
            return
        by_row: dict[int, list[int]] = {}
        for ix in indexes:
            if ix.isValid():
                by_row.setdefault(ix.row(), []).append(ix.column())
        lines: list[str] = []
        for r in sorted(by_row.keys()):
            col_list = sorted({c for c in by_row[r] if c != 0})
            if not col_list:
                continue
            parts: list[str] = []
            for c in col_list:
                t0 = self._table_model.cell_text(r, 0)
                oid = int(t0) if t0.isdigit() else None
                ok, txt = self._copy_text_for_table_cell(r, c, oid)
                parts.append(txt if ok else "")
            lines.append("\t".join(parts))
        text = "\n".join(lines)
        if not text.strip():
            self.status_label.setText("Copy: no copyable text in the selection.")
            return
        QApplication.clipboard().setText(text)
        self.status_label.setText("Copy: copied selection to clipboard.")

    def edit_paste(self) -> None:
        """Paste the clipboard into the current or primary selected cell."""
        ix = self.table.currentIndex()
        if not ix.isValid():
            sm = self.table.selectionModel()
            if sm is not None:
                for cand in sm.selectedIndexes():
                    if cand.isValid():
                        ix = cand
                        break
        if not ix.isValid():
            self.status_label.setText("Paste: select a cell first.")
            return
        row, col = ix.row(), ix.column()
        t0 = self._table_model.cell_text(row, 0)
        oid = int(t0) if t0.isdigit() else None
        if oid is None:
            self.status_label.setText("Paste: invalid row.")
            return
        clip = (QApplication.clipboard().text() or "").strip()
        if not clip:
            QMessageBox.information(self, "Paste", "Clipboard is empty.")
            return
        if col == CompoundTableModel.STRUCTURE_COL:
            if self._mol_from_structure_text(clip) is None:
                QMessageBox.warning(
                    self,
                    "Paste",
                    "Could not interpret the clipboard as a structure (try SMILES, InChI, or a MolBlock).",
                )
                return
        elif not self._table_model.column_accepts_text_edit(col):
            self.status_label.setText("Paste: this column cannot be edited.")
            return
        self._undo_stack.push(UndoPasteCellCommand(self, row, col, oid, clip))

    def _confirm_and_push_delete_rows(self, rows: list[int]) -> None:
        """Confirm then push UndoDeleteRowsCommand (shared by Edit and row header menu)."""
        rows = sorted({int(r) for r in rows})
        if not rows:
            QMessageBox.information(self, "Delete Selection", "No rows selected.")
            return
        title = "Delete Row" if len(rows) == 1 else "Delete Selection"
        msg = (
            "Delete this row? This cannot be undone except with Edit → Undo."
            if len(rows) == 1
            else f"Delete {len(rows)} selected rows? This cannot be undone except with Edit → Undo."
        )
        reply = QMessageBox.question(
            self,
            title,
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        cmd = UndoDeleteRowsCommand(self, rows)
        if cmd.snapshot_count() == 0:
            QMessageBox.information(self, "Delete Selection", "No valid rows to delete.")
            return
        self._undo_stack.push(cmd)

    def edit_delete_selection(self) -> None:
        """Remove every row that has at least one selected cell."""
        self._confirm_and_push_delete_rows(self._selected_logical_rows())

    def clear_table_after_confirm(self) -> None:
        """Clear the entire table only after explicit user confirmation."""
        reply = QMessageBox.question(
            self,
            "Clear Table",
            "Remove all rows, columns, filters, and molecules from this session?\n\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.clear_all()
            self.status_label.setText("Table cleared.")

    def _paste_clipboard_into_table_cell(
        self,
        row: int,
        col: int,
        oid: int | None,
        *,
        clip_text: str | None = None,
        quiet: bool = False,
    ) -> bool:
        if oid is None:
            return False
        if clip_text is not None:
            text = clip_text.strip()
        else:
            text = (QApplication.clipboard().text() or "").strip()
        if not text:
            if not quiet:
                QMessageBox.information(self, "Paste", "Clipboard is empty.")
            return False
        if col == CompoundTableModel.STRUCTURE_COL:
            mol = self._mol_from_structure_text(text)
            if mol is None:
                if not quiet:
                    QMessageBox.warning(
                        self,
                        "Paste",
                        "Could not interpret the clipboard as a structure (try SMILES, InChI, or a MolBlock).",
                    )
                return False
            self.mols[oid] = mol
            if "SMILES" in self.headers:
                try:
                    self._table_model.set_cell_text(oid, "SMILES", mol_to_canonical_smiles(mol))
                except Exception:
                    pass
            self._table_model.set_structure_pixmap(oid, None)
            self.calculate_global_bounds()
            self.apply_filters()
            if not quiet:
                self.status_label.setText("Structure updated from clipboard.")
            return True
        if not self._table_model.column_accepts_text_edit(col):
            return False
        h = self.headers[col]
        self._table_model.set_cell_text(oid, h, text)
        self.calculate_global_bounds()
        self.apply_filters()
        if not quiet:
            self.status_label.setText("Cell updated from clipboard.")
        return True

    def show_table_menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        row, col = idx.row(), idx.column()
        menu = QMenu(self)
        t0 = self._table_model.cell_text(row, 0)
        oid = int(t0) if t0.isdigit() else None
        mol = self.mols.get(oid) if oid is not None else None
        mol_row = self._mol_for_structure_row(row)

        packed_confs_b64 = None
        if 0 <= col < len(self.headers):
            hdr = self.headers[col]
            raw_cell = self._table_model.backing_value_for_row_header(row, hdr)
            packed_confs_b64 = resolve_blocks_b64_for_viewer(raw_cell, hdr, oid, getattr(self, "_confs_blocks_sidecar", {}))

        sketch_act = view_conformers_act = view3d_act = view2d_act = render2d_act = None
        structure_menu = False
        if mol_row is not None:
            sketch_act = menu.addAction("Open in Sketcher…")
            structure_menu = True
        if packed_confs_b64 is not None:
            view_conformers_act = menu.addAction("View Conformers…")
            structure_menu = True
        if mol_row is not None and packed_confs_b64 is None:
            view3d_act = menu.addAction("View in 3D…")
            structure_menu = True
        if mol_row is not None:
            view2d_act = menu.addAction("View in 2D…")
            render2d_act = menu.addAction(TOOL_RENDER_2D)
            render2d_act.setEnabled(oid is not None)
        if structure_menu:
            menu.addSeparator()

        can_copy, copy_text = self._copy_text_for_table_cell(row, col, oid)
        copy_act = menu.addAction("Copy")
        copy_act.setEnabled(can_copy)

        can_paste = oid is not None and (
            col == CompoundTableModel.STRUCTURE_COL or self._table_model.column_accepts_text_edit(col)
        )
        paste_act = menu.addAction("Paste")
        paste_act.setEnabled(can_paste)

        text_editable = self._table_model.column_accepts_text_edit(col)
        edit_act = clear_act = None
        if text_editable:
            menu.addSeparator()
            edit_act = menu.addAction("Edit Value…")
            clear_act = menu.addAction("Clear Value")

        action = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if action == sketch_act and mol_row is not None:
            self.open_sketcher(mol_row)
        elif view_conformers_act is not None and action == view_conformers_act and packed_confs_b64 is not None:
            from ..mol_viewer_3d import open_conformation_viewer_from_blocks_payload

            open_conformation_viewer_from_blocks_payload(
                self, packed_confs_b64, title="View Conformers", initial_superpose=False
            )
        elif view3d_act is not None and action == view3d_act and mol_row is not None:
            self.open_molecule_3d(mol_row)
        elif view2d_act is not None and action == view2d_act and mol_row is not None:
            self.open_molecule_2d(mol_row)
        elif render2d_act is not None and action == render2d_act and mol_row is not None:
            self.run_render_2d_for_table_row(row)
        elif action == copy_act and can_copy:
            QApplication.clipboard().setText(copy_text)
        elif action == paste_act and can_paste:
            clip = (QApplication.clipboard().text() or "").strip()
            if not clip:
                QMessageBox.information(self, "Paste", "Clipboard is empty.")
            elif col == CompoundTableModel.STRUCTURE_COL:
                if self._mol_from_structure_text(clip) is None:
                    QMessageBox.warning(
                        self,
                        "Paste",
                        "Could not interpret the clipboard as a structure (try SMILES, InChI, or a MolBlock).",
                    )
                else:
                    self._undo_stack.push(UndoPasteCellCommand(self, row, col, int(oid), clip))
            elif self._table_model.column_accepts_text_edit(col):
                self._undo_stack.push(UndoPasteCellCommand(self, row, col, int(oid), clip))
        elif edit_act is not None and action == edit_act:
            old_t = self._table_model.cell_text(row, col) or ""
            txt, ok = QInputDialog.getText(self, "Edit value", "New value:", text=old_t)
            if ok and oid is not None:
                h = self.headers[col]
                if txt != old_t:
                    self._undo_stack.push(UndoCellTextChangeCommand(self, int(oid), h, old_t, txt))
        elif clear_act is not None and action == clear_act:
            if oid is not None:
                h = self.headers[col]
                old_t = self._table_model.cell_text(row, col) or ""
                if old_t != "":
                    self._undo_stack.push(UndoCellTextChangeCommand(self, int(oid), h, old_t, ""))
    def _selected_smiles_strings(self) -> list[str]:
        """SMILES for PubChem/ChEMBL: canonical SMILES from any resolvable chemistry in each selected row."""
        if not self.headers:
            return []
        items = self.table.selectionModel().selectedIndexes()
        if not items:
            return []
        rows = sorted({i.row() for i in items})
        out: list[str] = []
        seen: set[str] = set()
        for r in rows:
            mol = self._mol_for_structure_row(r)
            if mol is None:
                continue
            try:
                smi = mol_to_canonical_smiles(mol).strip()
            except Exception:
                smi = ""
            if smi and smi not in seen:
                out.append(smi)
                seen.add(smi)
        return out

    def _ensure_columns(self, col_names: list[str]) -> None:
        """Ensure the table has these headers (adds columns to the right if needed)."""
        if not self.headers:
            self.headers = ["ID_HIDDEN", "Structure", "SMILES"]
            self._table_model.set_headers(list(self.headers))
            self.table.setColumnHidden(0, True)
        existing = {h: i for i, h in enumerate(self.headers)}
        for h in col_names:
            if h in existing:
                continue
            col_at = len(self.headers)
            self.headers.append(h)
            self._table_model.insert_column_at(col_at, h, None)
            existing[h] = col_at

    def add_row_from_external_record(self, smiles: str, fields: dict[str, str]) -> None:
        """Append a row with SMILES + additional fields; render structure when possible."""
        smiles = (smiles or "").strip()
        if not smiles:
            raise ValueError("Empty SMILES.")
        self._ensure_columns(["SMILES"] + list(fields.keys()))

        self.table.setSortingEnabled(False)
        oid = self.next_oid
        self.next_oid += 1
        row_cells: dict[str, str] = {}
        for h in self.headers[2:]:
            if h == "SMILES":
                row_cells[h] = smiles
            else:
                row_cells[h] = str(fields.get(h, "") or "")
        self._table_model.append_row(oid, row_cells)

        mol = Chem.MolFromSmiles(smiles)
        if mol is not None:
            self.mols[oid] = mol
            self.start_render_worker(oid, mol)

        self.calculate_global_bounds()
        self.table.setSortingEnabled(False)

    def clear_all(self):
        self._confs_blocks_sidecar = {}
        if getattr(self, "_undo_stack", None) is not None:
            self._undo_stack.clear()
        self._table_model.clear()
        if getattr(self, "_table_stack", None) is not None:
            self._table_stack.setCurrentIndex(1)
        self.zoomed_ids = set()
        for f in self.filters:
            f.deleteLater()
        self.filters, self.headers, self.mols, self.global_bounds = [], [], {}, {}
        self.next_oid = 0
        self._structure_field_override = None
        self._export_prep = None
        self._export_busy = False
        self._render2d_queue = None
        self._restore_render2d_batch_environment()
        self._session_restore_ctx = None
        self._csv_session_ctx = None
        self._session_load_generation = int(getattr(self, "_session_load_generation", 0)) + 1
        self._invalidate_substructure_async_jobs()
        self._pending_batches = []
        self._processing_batches = False
        self._last_batch_received = False
        self._ingest_loading = False
        self._structures_queued = 0
        self._import_progress_active = False
        self._import_render_done = 0
        self._import_render_goal = 0
        self._import_building_progress_shown = False
        self._clear_tool_progress()
        self._session_sort = None

    def _migrate_legacy_confs_cells_to_sidecar(self) -> None:
        """Move embedded v1 conformer payloads out of ``confs`` / ``superpose`` cells into ``_confs_blocks_sidecar``."""
        sc = getattr(self, "_confs_blocks_sidecar", None)
        if sc is None:
            self._confs_blocks_sidecar = {}
            sc = self._confs_blocks_sidecar
        cols = [c for c in ("confs", "superpose") if c in self.headers]
        if not cols:
            return
        n = self._table_model.rowCount()
        for r in range(n):
            t0 = self._table_model.cell_text(r, 0)
            if not t0.isdigit():
                continue
            oid = int(t0)
            for col in cols:
                raw = self._table_model.backing_value_for_row_header(r, col)
                light, b64 = demote_v1_cell_to_sidecar(raw, col)
                if b64 is not None:
                    sc[(oid, col)] = b64
                    if light != raw:
                        self._table_model.set_cell_text(oid, col, light)

    def _confs_sidecar_discard_oids(self, oids: list[int]) -> None:
        sc = getattr(self, "_confs_blocks_sidecar", None)
        if not sc or not oids:
            return
        dead = {int(o) for o in oids}
        for k in list(sc.keys()):
            if k[0] in dead:
                del sc[k]

    def _confs_sidecar_copy_for_new_row(self, src_oid: int, dst_oid: int) -> None:
        sc = getattr(self, "_confs_blocks_sidecar", None)
        if not sc:
            return
        for col in ("confs", "superpose"):
            b = sc.get((int(src_oid), col))
            if b:
                sc[(int(dst_oid), col)] = b

    def _export_cell_text(self, row: int, col: int) -> str:
        """Cell text for export: rehydrate ``confs`` / ``superpose`` so files stay self-contained."""
        if col == 1:
            return ""
        h = self.headers[col] if 0 <= col < len(self.headers) else ""
        if h in ("confs", "superpose"):
            raw = self._table_model.backing_value_for_row_header(row, h)
            t0 = self._table_model.cell_text(row, 0)
            oid = int(t0) if t0.isdigit() else -1
            if oid >= 0:
                sc = getattr(self, "_confs_blocks_sidecar", {}) or {}
                return rehydrate_v1_confs_cell(raw, h, oid, sc)
        return (self._table_cell_text(row, col) or "").strip()

    def _on_selection_browser_dialog_destroyed(self) -> None:
        self._selection_browser_dialog = None

    def open_selection_browser(self) -> None:
        """Open modeless dialog to walk selected rows with structure preview."""
        from ..selection_browser import SelectionBrowserDialog

        def _factory():
            return SelectionBrowserDialog(self)

        reuse_or_show_modeless_singleton(
            self,
            "_selection_browser_dialog",
            _factory,
            self._on_selection_browser_dialog_destroyed,
            on_reused_visible=lambda dlg: dlg.refresh_from_app(),
        )

