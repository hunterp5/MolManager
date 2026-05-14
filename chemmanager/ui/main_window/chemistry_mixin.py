from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

from PyQt5.QtCore import QItemSelection, QItemSelectionModel, QTimer, Qt
from PyQt5.QtGui import QImage, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
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

from ...config import load_config
from ...confs_codec import (
    demote_v1_cell_to_sidecar,
    rehydrate_v1_confs_cell,
    unpack_confs_blocks_json_b64,
)
from ...utils import mol_to_canonical_smiles, redact_sqlalchemy_url, safe_float, safe_mol_prop_string
from ..singleton_modeless_dialog import reuse_or_show_modeless_singleton
from ..strings import (
    LOADING_DETAIL_AFTER_FILE_READ,
    STATUS_READY_RENDER_2D,
    TOOL_CALCULATOR,
    TOOL_RENDER_2D,
    TOOL_RGROUP_DECOMP,
    loaded_sql_status,
)
from ...workers import (
    CalcWorker,
    ConformerGenerationWorker,
    CustomCalcWorker,
    ExportWorker,
    Render2DBatchProcessWorker,
    RGroupDecompositionWorker,
    SuperposeConformersWorker,
    UniversalLoadWorker,
    WashWorker,
    WorkerSignals,
)
from ..compound_table_model import (
    CompoundTableModel,
    CompoundTableView,
    STRUCTURE_COLUMN_HORIZONTAL_PADDING,
    STRUCTURE_DEPICT_HEIGHT,
    STRUCTURE_DEPICT_WIDTH,
)
from ..widgets import FilterCard, SubstructureFilterCard

logger = logging.getLogger(__name__)


class ChemistryMixin:
    def _sync_dialog_only_selected_scope(self, dialog: QDialog) -> None:
        """Refresh a tool dialog's scope checkbox label/count from the current table selection."""
        cb = getattr(dialog, "only_selected_cb", None)
        if cb is None:
            return
        prefix = getattr(dialog, "_only_selected_scope_prefix", "Only selected rows")
        n = len(self._selected_logical_rows())
        if n > 0:
            cb.setEnabled(True)
            cb.setText(f"{prefix} ({n} row(s))")
        else:
            cb.setEnabled(False)
            cb.setChecked(False)
            cb.setText(prefix)

    def _prepare_tool_dialog(self, dialog: QDialog) -> None:
        """Let the main table stay interactive and keep scope UI in sync while the dialog is open."""
        dialog.setModal(False)
        dialog.setWindowModality(Qt.NonModal)
        if getattr(dialog, "only_selected_cb", None) is None:
            return
        self._sync_dialog_only_selected_scope(dialog)
        sm = self.table.selectionModel()
        if sm is None:
            return

        def on_sel_changed(*_args):
            self._sync_dialog_only_selected_scope(dialog)

        sm.selectionChanged.connect(on_sel_changed)

        def on_finished(_result=None):
            try:
                sm.selectionChanged.disconnect(on_sel_changed)
            except TypeError:
                pass

        dialog.finished.connect(on_finished)

    def _abort_if_only_selected_but_empty(
        self, only_selected: bool, allowed: set | frozenset | None, title: str
    ) -> bool:
        """Return True if the user should stop (warning shown for empty selection)."""
        if only_selected and not allowed:
            QMessageBox.warning(
                self,
                title,
                "\u201cOnly selected rows\u201d is checked but nothing is selected.",
            )
            return True
        return False

    def on_file_loaded(self, mols_list, headers, is_first, is_last):
        # Append batch to pending queue and schedule incremental processing
        if is_first:
            self._clear_filter_target_smiles_cache()
            incoming = list(headers)
            if self._ingest_append_mode and self._table_model.rowCount() > 0:
                self._merge_import_headers(incoming)
            else:
                self.headers = incoming
                self.table.setSortingEnabled(False)
                self._table_model.clear_rows()
                self._table_model.set_headers(list(self.headers))
                self.table.setColumnHidden(0, True)
            self._structure_field_override = None
            struct_cols = [
                h for h in incoming[2:] if self._header_looks_structural(h)
            ]
            if len(struct_cols) >= 2:
                opt = "[Keep structures as loaded]"
                items = [opt] + struct_cols
                picked, ok = QInputDialog.getItem(
                    self,
                    "Structure source",
                    "Multiple structure-related columns were detected.\nWhich column should define the molecule used for the Structure column?",
                    items,
                    0,
                    False,
                )
                if ok and picked and picked != opt:
                    self._structure_field_override = picked
            if self._table_stack.currentIndex() == 0:
                self._loading_detail.setText(LOADING_DETAIL_AFTER_FILE_READ)
        if mols_list:
            self._pending_batches.append((mols_list, is_last))
        if is_last:
            self._last_batch_received = True
        # start processing loop if not running
        if not self._processing_batches:
            self._processing_batches = True
            QTimer.singleShot(0, self._process_next_chunk)

    def _process_next_chunk(self, chunk_size=64):
        # Process up to chunk_size molecules, then reschedule to keep UI responsive
        processed = 0
        while self._pending_batches and processed < chunk_size:
            mols_list, is_last = self._pending_batches.pop(0)
            # process as many as fits in remaining budget
            while mols_list and processed < chunk_size:
                m = mols_list.pop(0)
                m = self._apply_structure_field_override(m)
                oid = self.next_oid
                self.next_oid += 1
                self.mols[oid] = m
                self._table_model.append_row(oid, {})
                row_idx = self._table_model.rowCount() - 1
                self._fill_row_data_columns_from_mol(row_idx, m)
                # update status to show progress (table hidden behind loading page until complete)
                n = len(self.mols)
                self.status_label.setText(f"Loaded {n} molecules — preparing table…")
                if self._table_stack.currentIndex() == 0:
                    self._loading_detail.setText(f"Building table…\n{n} molecule(s); 2D structures draw when ready")
                if getattr(self, "_ingest_loading", False):
                    if not self._import_building_progress_shown:
                        self._import_building_progress_shown = True
                        self._on_tool_progress("Building table…", -1, -1)
                processed += 1
            # if there are remaining molecules in this batch, put them back to front
            if mols_list:
                self._pending_batches.insert(0, (mols_list, is_last))
                break
            # if this batch signaled last, mark last_batch_received
            if is_last:
                self._last_batch_received = True

        if self._pending_batches and processed >= chunk_size:
            # schedule next chunk to continue processing
            QTimer.singleShot(0, lambda: self._process_next_chunk(chunk_size))
        else:
            # finished current pending items; finalize if we have received last batch
            if not self._pending_batches and self._last_batch_received:
                self.calculate_global_bounds()
                self.table.setSortingEnabled(False)
                self._ingest_loading = False
                self._structures_queued = 0
                self._table_stack.setCurrentIndex(1)
                self._import_progress_active = False
                self._clear_tool_progress()
                self._ingest_append_mode = False
                started_render = self._try_auto_render_all_structures_after_ingest()
                if not started_render:
                    self.status_label.setText(STATUS_READY_RENDER_2D)
                self._last_batch_received = False
                if "confs" in self.headers or "superpose" in self.headers:
                    QTimer.singleShot(0, self._migrate_legacy_confs_cells_to_sidecar)
            self._processing_batches = False

    def _sync_structure_column_width_for_pixmap(self, pm: QPixmap | None, fallback_w: int) -> None:
        """Grow the Structure column to fit the pixmap + padding (never shrink — avoids fighting user resizes)."""
        col = CompoundTableModel.STRUCTURE_COL
        pad = STRUCTURE_COLUMN_HORIZONTAL_PADDING
        pix_w = int(pm.width()) if pm is not None and not pm.isNull() else int(fallback_w)
        need = max(1, pix_w + pad)
        cur = int(self.table.columnWidth(col))
        if need > cur:
            self.table.setColumnWidth(col, need)

    def _sync_data_pixmap_column_width(self, header_name: str, pm: QPixmap | None, fallback_w: int) -> None:
        try:
            col = self.headers.index(header_name)
        except ValueError:
            return
        pad = STRUCTURE_COLUMN_HORIZONTAL_PADDING
        pix_w = int(pm.width()) if pm is not None and not pm.isNull() else int(fallback_w)
        need = max(1, pix_w + pad)
        cur = int(self.table.columnWidth(col))
        if need > cur:
            self.table.setColumnWidth(col, need)

    def on_row_ready(self, idx, props, img, success, w, h, batch_session=None):
        rs = 0 if batch_session is None else int(batch_session)
        if rs != 0:
            cur = getattr(self, "_render2d_accept_session", None)
            if cur is None or rs != cur:
                return

        row = self._resolve_structure_row_for_oid(int(idx))
        oid = int(idx)
        pix_target = getattr(self, "_render2d_pixmap_target", None)
        batch = getattr(self, "_render2d_batch_active", False)
        if success and row != -1:
            if batch:
                self._render2d_pending[oid] = (bytes(img), True, int(w), int(h))
            else:
                pm = QPixmap.fromImage(QImage.fromData(img))
                if pix_target:
                    self._table_model.register_pixmap_column(pix_target)
                    self._table_model.set_column_pixmap(oid, pix_target, pm)
                else:
                    self._table_model.set_structure_pixmap(oid, pm)
                if self.table.rowHeight(row) != h:
                    self.table.setRowHeight(row, h)
                if pix_target:
                    self._sync_data_pixmap_column_width(pix_target, pm, w)
                else:
                    self._sync_structure_column_width_for_pixmap(pm, w)
                if props:
                    for name in self.headers[2:]:
                        if name in props:
                            self._table_model.set_cell_text(oid, name, str(props.get(name, "")))
                pend = getattr(self, "_structure_column_autosize_after_render_oid", None)
                if pend is not None and pend == oid:
                    self._structure_column_autosize_after_render_oid = None
                    try:
                        self.table.resizeColumnToContents(CompoundTableModel.STRUCTURE_COL)
                    except Exception:
                        pass
        else:
            if batch:
                self._render2d_pending[oid] = (b"", False, int(w), int(h))
            else:
                pend = getattr(self, "_structure_column_autosize_after_render_oid", None)
                if pend is not None and pend == oid:
                    self._structure_column_autosize_after_render_oid = None

        if getattr(self, "_import_progress_active", False) and self._import_render_goal > 0:
            self._import_render_done += 1
            done = min(self._import_render_done, self._import_render_goal)
            total_g = self._import_render_goal
            if getattr(self, "_render2d_batch_active", False):
                now = time.monotonic()
                last_t = float(getattr(self, "_render2d_progress_last_emit", 0.0))
                last_d = int(getattr(self, "_render2d_progress_last_done", 0))
                step = max(1, total_g // 50)
                emit = (
                    done <= 1
                    or done >= total_g
                    or (done - last_d) >= step
                    or (now - last_t) >= 0.12
                )
                if emit:
                    self._render2d_progress_last_emit = now
                    self._render2d_progress_last_done = done
                    self._on_tool_progress("Drawing 2D structures…", done, total_g)
            else:
                self._on_tool_progress("Drawing 2D structures…", done, total_g)
            if self._import_render_done >= self._import_render_goal:
                self._import_progress_active = False
                self._clear_tool_progress()
                self.status_label.setText("Ready")
                self._flush_render2d_batch_results()
                self._restore_render2d_batch_environment()

    def _flush_render2d_batch_results(self) -> None:
        """Apply buffered PNGs in one pass (table stayed on placeholders until workers finished)."""
        if not getattr(self, "_render2d_batch_active", False):
            return
        self._render2d_accept_session = None
        ordered = list(getattr(self, "_render2d_batch_oids_ordered", []) or [])
        pending = getattr(self, "_render2d_pending", None) or {}
        pix_target = getattr(self, "_render2d_pixmap_target", None)
        if pix_target:
            self._table_model.register_pixmap_column(pix_target)
        try:
            self.table.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            for oid in ordered:
                row = self._resolve_structure_row_for_oid(int(oid))
                rec = pending.get(oid)
                if not rec:
                    if pix_target:
                        self._table_model.set_column_pixmap(oid, pix_target, None)
                    else:
                        self._table_model.set_structure_pixmap(oid, None)
                    continue
                img_b, ok, rw, rh = rec
                if not ok or row < 0:
                    if pix_target:
                        self._table_model.set_column_pixmap(oid, pix_target, None)
                    else:
                        self._table_model.set_structure_pixmap(oid, None)
                    continue
                pm = QPixmap.fromImage(QImage.fromData(img_b))
                if pix_target:
                    self._table_model.set_column_pixmap(oid, pix_target, pm)
                else:
                    self._table_model.set_structure_pixmap(oid, pm)
                if row >= 0 and self.table.rowHeight(row) != rh:
                    self.table.setRowHeight(row, rh)
        finally:
            try:
                self.table.setUpdatesEnabled(True)
            except Exception:
                pass
        self._render2d_pending = {}
        self._render2d_batch_oids_ordered = []
        self._render2d_snapshot = None

    def on_cell_double_click(self, r, c):
        if c != 1:
            return
        t0 = self._table_model.cell_text(r, 0)
        if not t0.isdigit():
            return
        oid = int(t0)
        mol = self._mol_for_structure_row(r)
        if mol is None:
            self.status_label.setText("No structure available for this row.")
            return
        if oid in self.zoomed_ids:
            self.zoomed_ids.remove(oid)
            w, h = STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT
            self._structure_column_autosize_after_render_oid = oid
        else:
            self.zoomed_ids.add(oid)
            w, h = STRUCTURE_DEPICT_WIDTH * 2, STRUCTURE_DEPICT_HEIGHT * 2
            self._structure_column_autosize_after_render_oid = None
        self.start_render_worker(oid, mol, w, h)

    def run_disconnect_fragments(self) -> None:
        if not self.headers or not self.mols:
            return
        candidates = ["Structure"] + self._data_headers_confirmed_for_chemistry_tools()
        from ..dialogs import DisconnectFragmentsDialog

        n_sel = len(self._selected_logical_rows())
        dlg = DisconnectFragmentsDialog(candidates, self.headers, n_sel, self)
        self._prepare_tool_dialog(dlg)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.accepted.connect(lambda *_, d=dlg: self._on_disconnect_fragments_dialog_accepted(d))
        dlg.show()

    def _on_disconnect_fragments_dialog_accepted(self, dlg) -> None:
        src, replace_structure, new_pixmap_col, only_selected = dlg.config()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, "Disconnect Largest Fragments"):
            return
        self._disconnect_replace_structure = replace_structure
        self._disconnect_pixmap_column = None if replace_structure else new_pixmap_col
        self.status_label.setText("Disconnecting fragments…")
        if src == "Structure":
            if allowed is None:
                data = list(self.mols.items())
            else:
                data = []
                for oid in self._all_oids_in_table_order():
                    if oid not in allowed or oid not in self.mols:
                        continue
                    data.append((oid, self.mols[oid]))
            if not data:
                QMessageBox.information(
                    self,
                    "Disconnect Largest Fragments",
                    "No rows match the current scope and structure field.",
                )
                self.status_label.setText("Ready.")
                return
            self.process_queue.enqueue(
                "Disconnect largest fragments (structure)",
                lambda ev, d=data, s=self.signals: WashWorker(d, s, is_smiles=False, cancel_event=ev),
            )
        else:
            col = self.headers.index(src)
            data = []
            oids_walk = self._all_oids_in_table_order()
            if allowed is not None:
                oids_walk = [o for o in oids_walk if o in allowed]
            for oid in oids_walk:
                r = self.get_row_by_id(oid)
                if r == -1:
                    continue
                data.append((oid, self._table_cell_text(r, col)))
            if not data:
                QMessageBox.information(
                    self,
                    "Disconnect Largest Fragments",
                    "No rows match the current scope and structure field.",
                )
                self.status_label.setText("Ready.")
                return
            self.process_queue.enqueue(
                "Disconnect largest fragments (column)",
                lambda ev, d=data, s=self.signals: WashWorker(d, s, is_smiles=True, cancel_event=ev),
            )

    def _build_render2d_tasks_in_table_order(
        self,
        src: str,
        base_w: int,
        base_h: int,
        allowed_oids: set[int] | None = None,
    ) -> tuple[list, dict[int, int]]:
        """Collect (oid, mol, w, h) tasks in current visual row order (top to bottom) and oid→row map."""
        renders = []
        row_by_oid: dict[int, int] = {}
        col = None if src == "Structure" else self.headers.index(src)
        for r in range(self._table_model.rowCount()):
            t0 = self._table_model.cell_text(r, 0)
            if not t0.isdigit():
                continue
            oid = int(t0)
            if allowed_oids is not None and oid not in allowed_oids:
                continue
            if src == "Structure":
                mol = self.mols.get(oid)
                if mol is None:
                    continue
            else:
                if self._table_model.is_pixmap_data_column(src):
                    mol = self.mols.get(oid)
                    if mol is None:
                        raw = self._table_model.backing_value_for_row_header(r, src)
                        mol = self._mol_from_structure_text(raw) if raw else None
                    if mol is None:
                        mol = self._mol_for_structure_row(r)
                else:
                    raw = self._table_cell_text(r, col)
                    mol = self._mol_from_structure_text(raw)
                if mol is None:
                    continue
                self.mols[oid] = mol
            rw, rh = (
                (STRUCTURE_DEPICT_WIDTH * 2, STRUCTURE_DEPICT_HEIGHT * 2)
                if oid in self.zoomed_ids
                else (base_w, base_h)
            )
            renders.append((oid, mol, rw, rh))
            row_by_oid[oid] = r
        return renders, row_by_oid

    def _try_auto_render_all_structures_after_ingest(self) -> bool:
        """Queue 2D renders for every row with an in-memory Structure mol (after file ingest or SQL load)."""
        if getattr(self, "_render2d_batch_active", False):
            return False
        if not self.headers or self._table_model.rowCount() == 0:
            return False
        base_w, base_h = STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT
        renders, row_by_oid = self._build_render2d_tasks_in_table_order("Structure", base_w, base_h, None)
        if not renders:
            return False
        self._start_render_2d_batch(renders, row_by_oid, "Structure")
        return True

    def _restore_render2d_batch_environment(self) -> None:
        """Re-enable sorting and thread pool after a Render 2D run (or if cleared mid-batch)."""
        self._render2d_accept_session = None
        self._render2d_pending = {}
        self._render2d_batch_oids_ordered = []
        self._render2d_snapshot = None
        self._render2d_row_by_oid = None
        pix_target = getattr(self, "_render2d_pixmap_target", None)
        self._render2d_pixmap_target = None
        try:
            if pix_target:
                c = self.headers.index(pix_target)
                self.table.resizeColumnToContents(c)
            else:
                self.table.resizeColumnToContents(CompoundTableModel.STRUCTURE_COL)
        except Exception:
            pass
        try:
            self.table.setUpdatesEnabled(True)
        except Exception:
            pass
        if self._render2d_saved_sort_enabled is not None:
            try:
                self.table.setSortingEnabled(self._render2d_saved_sort_enabled)
            except Exception:
                pass
            self._render2d_saved_sort_enabled = None
        self._render2d_batch_active = False
        self._render2d_cancel_event = None
        hub = getattr(self, "background_activity", None)
        if hub is not None:
            hub.notify_changed()

    def cancel_render_2d_batch(self) -> bool:
        """Stop a Tools → Render 2D batch: no further chunks, workers skip drawing if not started."""
        if not self._render2d_batch_active:
            return False
        ev = getattr(self, "_render2d_cancel_event", None)
        if ev is not None:
            ev.set()
        self._render2d_queue = None
        self._render2d_accept_session = None
        self._render2d_pending.clear()
        self._render2d_batch_oids_ordered.clear()
        snap = getattr(self, "_render2d_snapshot", None)
        target = getattr(self, "_render2d_pixmap_target", None)
        if snap:
            for oid, pm in snap.items():
                if target:
                    self._table_model.set_column_pixmap(oid, target, pm)
                else:
                    self._table_model.set_structure_pixmap(oid, pm)
        self._render2d_snapshot = None
        self._import_progress_active = False
        self._clear_tool_progress()
        self._restore_render2d_batch_environment()
        return True

    def run_render_2d_structures(self) -> None:
        """Queue 2D structure renders for all rows (after deferred load)."""
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(self, TOOL_RENDER_2D, "Load a table with at least one row first.")
            return
        if self._render2d_batch_active:
            QMessageBox.warning(self, TOOL_RENDER_2D, "A render operation is already in progress.")
            return
        candidates = ["Structure"] + self._data_headers_confirmed_for_chemistry_tools()
        from ..dialogs import Render2DStructureDialog

        rd = Render2DStructureDialog(candidates, len(self._selected_logical_rows()), self)
        self._prepare_tool_dialog(rd)
        rd.setAttribute(Qt.WA_DeleteOnClose, True)
        rd.accepted.connect(lambda *_, d=rd: self._on_render_2d_dialog_accepted(d))
        rd.show()

    def _on_render_2d_dialog_accepted(self, rd) -> None:
        src = rd.chosen_source()
        only_selected = rd.only_selected_rows()
        allowed_oids = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed_oids, TOOL_RENDER_2D):
            return
        base_w, base_h = STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT
        renders, row_by_oid = self._build_render2d_tasks_in_table_order(src, base_w, base_h, allowed_oids)
        if not renders:
            QMessageBox.information(
                self,
                TOOL_RENDER_2D,
                "No valid structures were found for the selected source.",
            )
            self.status_label.setText("No structures rendered.")
            return
        self._start_render_2d_batch(renders, row_by_oid, src)

    def _start_render_2d_batch(self, renders, row_by_oid, src: str = "Structure") -> None:
        """Batch 2D renders: sorting stays off until done; workers use the normal render thread pool."""
        self._render2d_session_id += 1
        self._render2d_batch_session_tag = self._render2d_session_id
        self._render2d_accept_session = self._render2d_batch_session_tag
        self._render2d_pixmap_target = None if src == "Structure" else src
        if self._render2d_pixmap_target:
            self._table_model.register_pixmap_column(self._render2d_pixmap_target)
        self._render2d_saved_sort_enabled = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self._render2d_batch_active = True
        self._render2d_row_by_oid = row_by_oid
        oids = [oid for oid, _, _, _ in renders]
        self._render2d_batch_oids_ordered = oids
        self._render2d_pending = {}
        tgt = self._render2d_pixmap_target
        if tgt:
            self._render2d_snapshot = self._table_model.snapshot_column_pixmaps(tgt, oids)
            for oid in oids:
                self._table_model.set_column_pixmap(oid, tgt, None)
        else:
            self._render2d_snapshot = self._table_model.snapshot_structure_pixmaps(oids)
            for oid in oids:
                self._table_model.set_structure_pixmap(oid, None)
        self._render2d_progress_last_emit = 0.0
        self._render2d_progress_last_done = 0
        self._import_progress_active = True
        self._import_render_goal = len(renders)
        self._import_render_done = 0
        self._on_tool_progress("Drawing 2D structures…", 0, len(renders))
        self._render2d_cancel_event = threading.Event()
        self._render2d_queue = None
        hub = getattr(self, "background_activity", None)
        if hub is not None:
            hub.notify_changed()
        self._render_threadpool.start(
            Render2DBatchProcessWorker(
                renders,
                self.signals,
                self._render2d_cancel_event,
                self._render2d_batch_session_tag,
            )
        )

    def run_render_2d_for_table_row(self, row: int) -> None:
        """Run Tools → Render 2D for one row (Structure source), same pipeline as the full tool."""
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(self, TOOL_RENDER_2D, "Load a table with at least one row first.")
            return
        if self._render2d_batch_active:
            QMessageBox.warning(self, TOOL_RENDER_2D, "A render operation is already in progress.")
            return
        if row < 0 or row >= self._table_model.rowCount():
            return
        t0 = self._table_model.cell_text(row, 0)
        oid = int(t0) if t0.isdigit() else None
        if oid is None:
            QMessageBox.information(self, TOOL_RENDER_2D, "Could not resolve this row’s compound id.")
            return
        if self.mols.get(oid) is None:
            m = self._mol_for_structure_row(row)
            if m is None:
                QMessageBox.information(
                    self,
                    TOOL_RENDER_2D,
                    "No molecule is available for this row yet. Load SMILES or add a structure first.",
                )
                return
            self.mols[oid] = m
        base_w, base_h = STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT
        renders, row_by_oid = self._build_render2d_tasks_in_table_order(
            "Structure", base_w, base_h, {oid}
        )
        if not renders:
            QMessageBox.information(
                self,
                TOOL_RENDER_2D,
                "No molecule is available for this row yet. Load SMILES or add a structure first.",
            )
            return
        self._start_render_2d_batch(renders, row_by_oid, "Structure")

    def on_wash_finished(self, results):
        self.table.setSortingEnabled(False)
        if "Fragments" not in self.headers:
            if "Salt" in self.headers:
                idx_old = self.headers.index("Salt")
                self.headers[idx_old] = "Fragments"
                self._table_model.rename_header_at(idx_old, "Fragments")
            else:
                self.headers.insert(2, "Fragments")
                self._table_model.insert_column_at(2, "Fragments", None)

        replace_structure = getattr(self, "_disconnect_replace_structure", True)
        pixmap_col = getattr(self, "_disconnect_pixmap_column", None)
        self._disconnect_replace_structure = True
        self._disconnect_pixmap_column = None

        if not replace_structure and pixmap_col and pixmap_col not in self.headers:
            nc = self._table_model.columnCount()
            self.headers.append(pixmap_col)
            self._table_model.insert_column_at(nc, pixmap_col, None)

        smiles_h = self._canonical_smiles_header_for_updates()

        for oid, mol, fragments in results:
            self.mols[oid] = mol
            row = self.get_row_by_id(oid)
            if row != -1:
                if replace_structure:
                    self._table_model.set_structure_pixmap(oid, None)
                elif pixmap_col:
                    self._table_model.set_cell_text(oid, pixmap_col, mol_to_canonical_smiles(mol))
                self._table_model.set_cell_text(oid, "Fragments", fragments)
                if smiles_h is not None:
                    self._table_model.set_cell_text(oid, smiles_h, mol_to_canonical_smiles(mol))
        self.calculate_global_bounds()
        self.table.setSortingEnabled(False)
        self._clear_tool_progress()
        if results and not getattr(self, "_render2d_batch_active", False):
            base_w, base_h = STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT
            renders = []
            row_by_oid: dict[int, int] = {}
            for oid, mol, _frag in results:
                if mol is None:
                    continue
                row = self.get_row_by_id(oid)
                if row < 0:
                    continue
                rw, rh = (
                    (STRUCTURE_DEPICT_WIDTH * 2, STRUCTURE_DEPICT_HEIGHT * 2)
                    if oid in self.zoomed_ids
                    else (base_w, base_h)
                )
                renders.append((oid, mol, rw, rh))
                row_by_oid[oid] = row
            if renders:
                self._start_render_2d_batch(renders, row_by_oid, "Structure")
                return
        self.status_label.setText("Done.")

    def open_generate_conformations(self):
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                "Generate Conformations",
                "Open a file or add rows so the table has molecules to process.",
            )
            return
        from ..dialogs import GenerateConformationsDialog

        d = GenerateConformationsDialog(len(self._selected_logical_rows()), self)
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(lambda *_, dlg=d: self._on_generate_conformations_dialog_accepted(dlg))
        d.show()

    def _on_generate_conformations_dialog_accepted(self, d) -> None:
        only_selected = d.only_selected_rows()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, "Generate Conformations"):
            return
        oids_list = self._all_oids_in_table_order()
        if allowed is not None:
            oids_list = [o for o in oids_list if o in allowed]
        data = []
        for o in oids_list:
            r = self.get_row_by_id(o)
            m = self.mols.get(o) if r >= 0 else None
            if m is None and r >= 0:
                m = self._mol_for_structure_row(r)
            if m is not None:
                data.append((o, m))
        if not data:
            QMessageBox.information(
                self,
                "Generate Conformations",
                "No parseable structures for those rows (in-memory molecules or chemistry in table cells).",
            )
            return
        params = d.params()
        n = len(data)
        self.status_label.setText("Generating conformations…")
        self.process_queue.enqueue(
            f"Generate conformations ({n} structures)",
            lambda ev, d=data, p=params, sigs=self.signals: ConformerGenerationWorker(
                d, p, sigs, cancel_event=ev
            ),
        )

    def cancel_active_tool_process(self) -> None:
        """Request cooperative cancellation of the process-queue job and/or an active Render 2D batch."""
        r2d = self.cancel_render_2d_batch()
        pq_ok = self.process_queue.cancel_running()
        if pq_ok:
            self.status_label.setText("Cancelling…")
        elif r2d:
            self.status_label.setText("Render 2D cancelled.")
        else:
            QMessageBox.information(
                self,
                "Cancel Process",
                "Nothing to cancel (no process-queue job or Render 2D batch), or cancellation was already requested.",
            )

    def on_conformers_finished(self, results: list) -> None:
        self._clear_tool_progress()
        self.table.setSortingEnabled(False)
        try:
            self.table.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            if "confs" not in self.headers:
                col_at = len(self.headers)
                self.headers.append("confs")
                self._table_model.insert_column_at(col_at, "confs", None)
            pairs: list[tuple[int, str]] = []
            sc = getattr(self, "_confs_blocks_sidecar", None)
            if sc is None:
                self._confs_blocks_sidecar = {}
                sc = self._confs_blocks_sidecar
            for item in results:
                if len(item) < 3:
                    continue
                oid, cell = int(item[0]), str(item[2] or "")
                light, b64 = demote_v1_cell_to_sidecar(cell, "confs")
                if b64 is not None:
                    sc[(oid, "confs")] = b64
                pairs.append((oid, light))
            if pairs:
                self._table_model.set_column_text_by_oids("confs", pairs)
            self.calculate_global_bounds()
            self.table.setSortingEnabled(False)
        finally:
            try:
                self.table.setUpdatesEnabled(True)
            except Exception:
                pass
        self.status_label.setText("Done.")

    def open_superpose_conformers(self):
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                "Superpose Conformers",
                "Open a file or add rows so the table has data to process.",
            )
            return
        if "confs" not in self.headers:
            QMessageBox.information(
                self,
                "Superpose Conformers",
                'Add a "confs" column first by running Generate Conformations (packed multi-conformer cells).',
            )
            return
        from ..dialogs import SuperposeConformersDialog

        d = SuperposeConformersDialog(len(self._selected_logical_rows()), self)
        self._prepare_tool_dialog(d)
        if d.exec_() != QDialog.Accepted:
            return
        only_selected = d.only_selected_rows()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, "Superpose Conformers"):
            return
        oids_list = self._all_oids_in_table_order()
        if allowed is not None:
            oids_list = [o for o in oids_list if o in allowed]
        data: list[tuple[int, str]] = []
        for o in oids_list:
            r = self.get_row_by_id(o)
            if r < 0:
                continue
            raw = self._table_model.backing_value_for_row_header(r, "confs")
            sc = getattr(self, "_confs_blocks_sidecar", {}) or {}
            full = rehydrate_v1_confs_cell(raw, "confs", int(o), sc)
            if unpack_confs_blocks_json_b64(full) is None:
                continue
            data.append((o, full))
        if not data:
            QMessageBox.information(
                self,
                "Superpose Conformers",
                "No rows in scope have a packed multi-conformer \"confs\" cell. Run Generate Conformations first.",
            )
            return
        params = d.params()
        n = len(data)
        self.status_label.setText("Superposing conformers…")
        self.process_queue.enqueue(
            f"Superpose conformers ({n} rows)",
            lambda ev, d=data, p=params, sigs=self.signals: SuperposeConformersWorker(
                d, p, sigs, cancel_event=ev
            ),
        )

    def on_superpose_finished(self, results: list) -> None:
        self._clear_tool_progress()
        self.table.setSortingEnabled(False)
        try:
            self.table.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            if "superpose" not in self.headers:
                col_at = len(self.headers)
                self.headers.append("superpose")
                self._table_model.insert_column_at(col_at, "superpose", None)
            pairs: list[tuple[int, str]] = []
            sc = getattr(self, "_confs_blocks_sidecar", None)
            if sc is None:
                self._confs_blocks_sidecar = {}
                sc = self._confs_blocks_sidecar
            for item in results:
                if len(item) < 3:
                    continue
                oid, cell = int(item[0]), str(item[2] or "")
                light, b64 = demote_v1_cell_to_sidecar(cell, "superpose")
                if b64 is not None:
                    sc[(oid, "superpose")] = b64
                pairs.append((oid, light))
            if pairs:
                self._table_model.set_column_text_by_oids("superpose", pairs)
            self.calculate_global_bounds()
            self.table.setSortingEnabled(False)
        finally:
            try:
                self.table.setUpdatesEnabled(True)
            except Exception:
                pass
        self.status_label.setText("Done.")

    def open_calc(self):
        if not self.headers:
            return
        from ..dialogs import PropertyDialog

        desc_src_cols = ["Structure"] + self._data_headers_confirmed_for_chemistry_tools()
        d = PropertyDialog(desc_src_cols, len(self._selected_logical_rows()), self)
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(lambda *_, dlg=d: self._on_calc_descriptors_dialog_accepted(dlg))
        d.show()

    def _on_calc_descriptors_dialog_accepted(self, d) -> None:
        disp, fns = d.get_selected()
        src = d.src_combo.currentText()
        is_s = src != "Structure"
        s_idx = self.headers.index(src)
        only_selected = d.only_selected_rows()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, "Calculate Descriptors"):
            return
        oids_list = self._all_oids_in_table_order()
        if allowed is not None:
            oids_list = [o for o in oids_list if o in allowed]
        self.status_label.setText("Calculating...")
        if not is_s:
            data = []
            for o in oids_list:
                r = self.get_row_by_id(o)
                m = self.mols.get(o) if r >= 0 else None
                if m is None and r >= 0:
                    m = self._mol_for_structure_row(r)
                if m is not None:
                    data.append((o, m))
        else:
            data = [(o, self._table_cell_text(self.get_row_by_id(o), s_idx)) for o in oids_list]
        if not data:
            QMessageBox.information(
                self,
                "Calculate Descriptors",
                "No rows to process for this scope and source.",
            )
            self.status_label.setText("Ready.")
            return
        self.process_queue.enqueue(
            f"Calculate descriptors ({len(data)} rows)",
            lambda ev, d=data, dh=disp, fn=fns, sm=is_s, sigs=self.signals: CalcWorker(
                d, dh, fn, sm, sigs, cancel_event=ev
            ),
        )

    def on_calc_finished(self, res, calc_h):
        self._clear_tool_progress()
        self.table.setSortingEnabled(False)
        try:
            self.table.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            h_map = {h: i for i, h in enumerate(self.headers)}
            new_h = [h for h in calc_h if h not in h_map]
            if new_h:
                for nh in new_h:
                    col_at = len(self.headers)
                    self.headers.append(nh)
                    self._table_model.insert_column_at(col_at, nh, None)
                    h_map[nh] = col_at
            for oid, row_d in res:
                if self.get_row_by_id(oid) < 0:
                    continue
                self._table_model.set_cell_text_batch(
                    oid,
                    {h: str(row_d.get(h, "N/A")) for h in calc_h},
                )
            self.calculate_global_bounds()
            self.table.setSortingEnabled(False)
        finally:
            try:
                self.table.setUpdatesEnabled(True)
            except Exception:
                pass
        self.status_label.setText("Done.")

    def open_rgroup_decomposition(self) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                TOOL_RGROUP_DECOMP,
                "Load a table with at least one row first.",
            )
            return
        candidates = ["Structure"] + self._data_headers_confirmed_for_chemistry_tools()
        from ..dialogs import RGroupDecompositionDialog

        d = RGroupDecompositionDialog(candidates, len(self._selected_logical_rows()), self)
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(lambda *_, dlg=d: self._on_rgroup_decomposition_dialog_accepted(dlg))
        d.show()

    def _on_rgroup_decomposition_dialog_accepted(self, d) -> None:
        p = d.params()
        only_selected = d.only_selected_rows()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, TOOL_RGROUP_DECOMP):
            return
        src = p.structure_source
        col = None if src == "Structure" else self.headers.index(src)
        data: list[tuple[int, Chem.Mol]] = []
        for r in range(self._table_model.rowCount()):
            t0 = self._table_model.cell_text(r, 0)
            if not t0.isdigit():
                continue
            oid = int(t0)
            if allowed is not None and oid not in allowed:
                continue
            if src == "Structure":
                mol = self.mols.get(oid)
                if mol is None:
                    mol = self._mol_for_structure_row(r)
            else:
                if self._table_model.is_pixmap_data_column(src):
                    mol = self.mols.get(oid)
                    if mol is None:
                        raw = self._table_model.backing_value_for_row_header(r, src)
                        mol = self._mol_from_structure_text(raw) if raw else None
                    if mol is None:
                        mol = self._mol_for_structure_row(r)
                else:
                    raw = self._table_cell_text(r, col)
                    mol = self._mol_from_structure_text(raw)
                if mol is not None:
                    self.mols[oid] = mol
            if mol is not None:
                data.append((oid, mol))
        if not data:
            QMessageBox.information(
                self,
                TOOL_RGROUP_DECOMP,
                "No valid structures were found for the selected source and scope.",
            )
            self.status_label.setText("Ready.")
            return
        self.status_label.setText("R-group decomposition…")
        self.process_queue.enqueue(
            f"R-group decomposition ({len(data)} rows)",
            lambda ev, dt=data, pp=p, sigs=self.signals: RGroupDecompositionWorker(
                dt,
                pp.core_query,
                pp.column_prefix,
                pp.only_match_at_r_groups,
                pp.remove_hydrogens_post_match,
                pp.matching,
                sigs,
                cancel_event=ev,
            ),
        )

    def on_rgroup_decomp_finished(self, res, col_headers: list) -> None:
        self.on_calc_finished(res, col_headers)

    def on_rgroup_decomp_failed(self, message: str) -> None:
        self._clear_tool_progress()
        self.status_label.setText("Ready.")
        QMessageBox.warning(self, TOOL_RGROUP_DECOMP, message or "R-group decomposition failed.")

    def _on_calculator_dialog_finished(self, result: int) -> None:
        from ..dialogs import CalculatorDialog

        dlg = self.sender()
        if not isinstance(dlg, CalculatorDialog) or int(result) != int(QDialog.Accepted):
            return
        self.new_c = dlg.name_input.text()
        only_selected = dlg.only_selected_rows()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, TOOL_CALCULATOR):
            return
        self.status_label.setText("Calculating…")
        numeric_vars = list(self.global_bounds.keys())
        h_map = {h: i for i, h in enumerate(self.headers)}
        oids_list = self._all_oids_in_table_order()
        if allowed is not None:
            oids_list = [o for o in oids_list if o in allowed]
        row_data = [
            (
                o,
                {v: (self._table_cell_text(self.get_row_by_id(o), h_map[v]) or "0") for v in numeric_vars},
            )
            for o in oids_list
        ]
        if not row_data:
            QMessageBox.information(
                self,
                TOOL_CALCULATOR,
                "No rows to process for this scope.",
            )
            self.status_label.setText("Ready.")
            return
        expr = dlg.expr_input.text()
        self.process_queue.enqueue(
            f"Calculator ({len(row_data)} rows)",
            lambda ev, rd=row_data, ex=expr, sigs=self.signals: CustomCalcWorker(rd, ex, sigs, cancel_event=ev),
        )

    def open_calculator(self):
        if not self.headers:
            return
        if load_config().disable_custom_calc:
            QMessageBox.information(
                self,
                TOOL_CALCULATOR,
                "The calculator is disabled by policy (environment variable CHEMMANAGER_DISABLE_CUSTOM_CALC).",
            )
            return
        numeric_vars = list(self.global_bounds.keys())
        from ..dialogs import CalculatorDialog

        def _factory():
            d = CalculatorDialog(numeric_vars, len(self._selected_logical_rows()), self)
            d.setModal(False)
            d.setAttribute(Qt.WA_DeleteOnClose, True)
            d.finished.connect(self._on_calculator_dialog_finished)
            self._prepare_tool_dialog(d)
            return d

        reuse_or_show_modeless_singleton(
            self,
            "_calculator_dialog",
            _factory,
            self._on_calculator_dialog_destroyed,
            on_reused_visible=lambda dlg: self._sync_dialog_only_selected_scope(dlg),
        )

    def _on_plot_dialog_destroyed(self):
        self._plot_dialog = None

    def _on_sketcher_dialog_destroyed(self):
        self._sketcher_dialog = None

    def _on_calculator_dialog_destroyed(self):
        self._calculator_dialog = None

    def _on_data_analysis_dialog_destroyed(self):
        self._data_analysis_dialog = None

    def open_data_analysis(self):
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(self, "Data", "Open a file or add rows so the table has data to analyze.")
            return
        from ..data_analysis import DataAnalysisDialog

        reuse_or_show_modeless_singleton(
            self,
            "_data_analysis_dialog",
            lambda: DataAnalysisDialog(self),
            self._on_data_analysis_dialog_destroyed,
        )

    def open_plot(self):
        if not self.headers:
            return
        reuse_or_show_modeless_singleton(
            self,
            "_plot_dialog",
            lambda: self._plot_dialog_factory(),
            self._on_plot_dialog_destroyed,
            on_reused_visible=lambda dlg: self._sync_dialog_only_selected_scope(dlg),
        )

    def _plot_dialog_factory(self):
        from ..plot import PlotDialog

        d = PlotDialog(self)
        self._prepare_tool_dialog(d)
        return d

    def open_sketcher(self, mol=None):
        # QAction.triggered passes False; never treat that as a molecule.
        if mol is not None and not isinstance(mol, Chem.Mol):
            mol = None
        from ..sketcher import SketcherDialog

        def _on_reuse(dlg):
            if mol is not None:
                dlg.load_structure_from_mol(mol)

        reuse_or_show_modeless_singleton(
            self,
            "_sketcher_dialog",
            lambda: SketcherDialog(self, initial_mol=mol),
            self._on_sketcher_dialog_destroyed,
            on_reused_visible=_on_reuse if mol is not None else None,
        )

    def open_molecule_3d(self, mol=None):
        if mol is not None and not isinstance(mol, Chem.Mol):
            mol = None
        if mol is None:
            return
        from ..mol_viewer_3d import open_molecule_3d_viewer

        open_molecule_3d_viewer(mol, self, title="View in 3D")

    def open_molecule_2d(self, mol=None):
        if mol is not None and not isinstance(mol, Chem.Mol):
            mol = None
        if mol is None:
            return
        from ..mol_viewer_3d import open_molecule_2d_viewer

        open_molecule_2d_viewer(mol, self, title="View in 2D")

    def open_external_db(self):
        from ..external import ExternalDBDialog

        reuse_or_show_modeless_singleton(
            self,
            "_external_db_dialog",
            lambda: ExternalDBDialog(self),
            self._on_external_db_dialog_destroyed,
        )

    def open_pubchem(self):
        from ..external import PubChemDialog

        reuse_or_show_modeless_singleton(
            self,
            "_pubchem_dialog",
            lambda: PubChemDialog(self),
            self._on_pubchem_dialog_destroyed,
        )

    def open_chembl(self):
        from ..external import ChEMBLDialog

        reuse_or_show_modeless_singleton(
            self,
            "_chembl_dialog",
            lambda: ChEMBLDialog(self),
            self._on_chembl_dialog_destroyed,
        )

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

    def load_from_sql(
        self,
        *,
        url: str,
        query: str | None = None,
        table: str | None = None,
        limit: int = 50000,
        apply_limit: bool = True,
        clear_first: bool = True,
    ) -> None:
        f"""Load a SQL query/table into the main table.

        If a 'SMILES' column exists (case-insensitive), molecules will be created and
        2D structure images are drawn automatically (same as after opening a structure file).
        """
        try:
            import pandas as pd
        except Exception as e:
            raise RuntimeError("pandas is required for SQL loading. Install requirements.txt.") from e

        try:
            from sqlalchemy import create_engine, text
        except Exception as e:
            raise RuntimeError("sqlalchemy is required for SQL loading. Install requirements.txt.") from e

        if bool(query) == bool(table):
            raise ValueError("Provide exactly one of: query or table.")

        if table is not None:
            tname = str(table).strip()
            if re.fullmatch(r"[A-Za-z0-9_]+", tname) is None:
                raise ValueError(
                    "SQL table name may only contain letters, digits, and underscores (identifier guard)."
                )
            table = tname

        sql_cfg = load_config()
        hard_cap = sql_cfg.sql_max_rows_hard
        precowarn = sql_cfg.sql_precount_warn
        try:
            li = int(limit) if limit is not None else 0
        except (TypeError, ValueError):
            li = 0
        if li > hard_cap:
            li = hard_cap
        if li < 0:
            li = 0

        logger.debug("load_from_sql url=%s", redact_sqlalchemy_url(url))

        connect_args: dict = {}
        lu = url.lower().strip()
        if lu.startswith("sqlite"):
            t_s = sql_cfg.sqlite_timeout_s
            connect_args["timeout"] = max(1.0, min(t_s, 300.0))
        elif "postgresql" in lu or lu.startswith("postgres"):
            ct = sql_cfg.pg_connect_timeout
            connect_args["connect_timeout"] = max(1, min(ct, 120))

        eng_kw = {}
        if connect_args:
            eng_kw["connect_args"] = connect_args
        eng = create_engine(url, **eng_kw)
        with eng.connect() as conn:
            limit_eff = int(li) if apply_limit and li else 0

            if apply_limit and limit_eff > 0 and precowarn > 0:
                est = None
                try:
                    if table:
                        crow = conn.execute(text(f"SELECT COUNT(*) AS c FROM {table}")).mappings().first()
                        est = int(crow["c"]) if crow and crow.get("c") is not None else None
                    else:
                        base = (query or "").strip().rstrip(";")
                        if base:
                            crow = conn.execute(
                                text(f"SELECT COUNT(*) AS c FROM ({base}) AS __chem_cnt")
                            ).mappings().first()
                            est = int(crow["c"]) if crow and crow.get("c") is not None else None
                except Exception:
                    est = None
                if est is not None and est >= precowarn:
                    r = QMessageBox.question(
                        self,
                        "Large SQL result",
                        f"The data source reports about {est:,} row(s). Up to {limit_eff:,} row(s) will be fetched, "
                        "which may use significant time and memory.\n\nContinue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if r != QMessageBox.Yes:
                        return

            if table:
                sql = f"SELECT * FROM {table}"
                if apply_limit and limit_eff:
                    sql += f" LIMIT {int(limit_eff)}"
                df = pd.read_sql_query(text(sql), conn)
            else:
                sql = query or ""
                if apply_limit and limit_eff:
                    # If the query already includes a LIMIT, leave it alone.
                    if re.search(r"\blimit\b", sql, flags=re.IGNORECASE) is None:
                        sql = f"SELECT * FROM ({sql}) AS subq LIMIT {int(limit_eff)}"
                df = pd.read_sql_query(text(sql), conn)

        if df is None or df.empty:
            raise RuntimeError("Query returned 0 rows.")

        if apply_limit and limit_eff and len(df) >= limit_eff:
            QMessageBox.information(
                self,
                "SQL load",
                f"The result has {len(df):,} row(s), reaching the row limit ({limit_eff:,}). "
                "If you expected more rows, raise “Max rows” in the SQL dialog or adjust your query.",
            )

        if clear_first:
            self.clear_all()

        # Build headers: keep the app's first two columns.
        cols = [str(c) for c in df.columns]
        self.headers = ["ID_HIDDEN", "Structure"] + cols
        self.table.setSortingEnabled(False)
        self._table_model.clear_rows()
        self._table_model.set_headers(list(self.headers))
        self.table.setColumnHidden(0, True)

        smiles_col = None
        for c in cols:
            if c.lower() == "smiles":
                smiles_col = c
                break

        # Reset molecule store.
        self.mols = {}
        self._clear_filter_target_smiles_cache()
        self.global_bounds = {}
        self.next_oid = 0

        # Insert rows (NumPy slice is much faster than df.iterrows for large frames).
        raw = df[cols].to_numpy(dtype=object, copy=False)
        n_df = len(df)
        si = cols.index(smiles_col) if smiles_col else None
        for i in range(n_df):
            oid = self.next_oid
            self.next_oid += 1
            row_cells = {}
            for j, c in enumerate(cols):
                v = raw[i, j]
                row_cells[c] = "" if v is None or pd.isna(v) else str(v)
            self._table_model.append_row(oid, row_cells)

            if smiles_col is not None and si is not None:
                smi = row_cells.get(smiles_col, "") or ""
                smi = smi.strip()
                mol = Chem.MolFromSmiles(smi) if smi else None
                if mol is not None:
                    self.mols[oid] = mol

        self.calculate_global_bounds()
        self.table.setSortingEnabled(False)
        nrows = n_df
        if smiles_col and self._try_auto_render_all_structures_after_ingest():
            self.status_label.setText(f"Loaded {nrows} row(s) from SQL — drawing 2D structures…")
        else:
            self.status_label.setText(
                loaded_sql_status(nrows) if smiles_col else f"Loaded {nrows} row(s) from SQL (no SMILES column)."
            )

    def open_fp_similarity(self):
        if not self.headers:
            return
        from ..dialogs import FPSimilarityDialog

        dlg = FPSimilarityDialog(self)
        self._prepare_tool_dialog(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_pka_predictor(self) -> None:
        if not self.headers:
            QMessageBox.information(
                self,
                "pKa Predictor",
                "Open a file or start a session first.",
            )
            return
        from ..dialogs import PKaPredictorDialog

        dlg = PKaPredictorDialog(self)
        self._prepare_tool_dialog(dlg)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_protomer_generator(self) -> None:
        if not self.headers:
            QMessageBox.information(
                self,
                "Generate Protomers",
                "Open a file or start a session first.",
            )
            return
        from ..dialogs import ProtomerGeneratorDialog

        dlg = ProtomerGeneratorDialog(self)
        self._prepare_tool_dialog(dlg)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def on_custom_calc_finished(self, res):
        # If the expression failed for every row, don't add an all-error column.
        ok_any = False
        for _idx, val in res:
            t = (val or "").strip()
            if safe_float(t) is not None:
                ok_any = True
                break
        if not ok_any:
            QMessageBox.warning(
                self,
                TOOL_CALCULATOR,
                "The expression produced no numeric results (all rows failed). No column was added.",
            )
            self.status_label.setText(f"{TOOL_CALCULATOR}: no numeric results.")
            self._clear_tool_progress()
            return

        self._clear_tool_progress()
        self.table.setSortingEnabled(False)
        nc = self._table_model.columnCount()
        self.headers.append(self.new_c)
        self._table_model.insert_column_at(nc, self.new_c, None)
        # One batched model update (contiguous row runs) vs N× set_cell_text.
        self._table_model.set_column_text_by_oids(self.new_c, [(oid, str(val)) for oid, val in res])
        self.calculate_global_bounds()
        self.table.setSortingEnabled(False)
        self.status_label.setText("Done.")

