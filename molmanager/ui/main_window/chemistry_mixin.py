from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import nullcontext

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QDialog,
    QMessageBox,
    QVBoxLayout,
)

from rdkit import Chem

from ...config import load_config
from ...display_constants import STRUCTURE_ROW_DEFAULT_HEIGHT
from ...structure_render_store import StructureRenderStore
from ...confs_codec import (
    demote_v1_cell_to_sidecar,
    rehydrate_v1_confs_cell,
    unpack_confs_blocks_json_b64,
)
from ...utils import mol_to_canonical_smiles, redact_sqlalchemy_url, safe_float
from ..singleton_modeless_dialog import reuse_or_show_modeless_singleton
from ..strings import (
    LOADING_DETAIL_AFTER_FILE_READ,
    STATUS_READY_RENDER_2D,
    TOOL_CALCULATOR,
    TOOL_SINGLE_CONFORMATION,
    TOOL_RENDER_2D,
    TOOL_BRICS_DECOMP,
    TOOL_BRICS_RECOMP,
    TOOL_CORE_DECOMP,
    TOOL_RECAP_DECOMP,
    TOOL_RECAP_RECOMP,
    loaded_session_status,
    loaded_sql_status,
)
from ...storage import SqliteTableStore
from ...workers import (
    CalcWorker,
    ConformerGenerationWorker,
    CustomCalcWorker,
    FragmentDecompositionWorker,
    FragmentRecompositionWorker,
    Render2DBatchProcessWorker,
    RGroupDecompositionWorker,
    SqliteRebuildWorker,
    SuperposeConformersWorker,
    Render2DBatchHeldJob,
    WashWorker,
)
from ..compound_table_model import (
    CompoundTableModel,
    STRUCTURE_COLUMN_HORIZONTAL_PADDING,
    STRUCTURE_DEPICT_HEIGHT,
    STRUCTURE_DEPICT_WIDTH,
)
from ..widgets import CategoryFilterCard, FilterCard, TextFilterCard

logger = logging.getLogger(__name__)


class ChemistryMixin:
    def _sync_dialog_only_selected_scope(self, dialog: QDialog) -> None:
        """Refresh a tool dialog's scope checkbox label/count from the current table selection."""
        cb = getattr(dialog, "only_selected_cb", None)
        if cb is None:
            return
        try:
            from PyQt5 import sip

            if sip.isdeleted(cb):
                return
        except Exception:
            pass
        prefix = getattr(dialog, "_only_selected_scope_prefix", "Only selected rows")
        n = len(self._selected_logical_rows())
        try:
            if n > 0:
                cb.setEnabled(True)
                cb.setText(f"{prefix} ({n} row(s))")
            else:
                cb.setEnabled(False)
                cb.setChecked(False)
                cb.setText(prefix)
        except RuntimeError:
            return

    def _prepare_tool_dialog(self, dialog: QDialog) -> None:
        """Let the main table stay interactive and keep scope UI in sync while the dialog is open."""
        dialog.setModal(False)
        dialog.setWindowModality(Qt.NonModal)
        self._attach_tool_scope_sync(dialog, on_finished_signal=dialog.finished)

    def _prepare_tool_plot(self, plot_widget) -> None:
        """Keep docked plot scope UI in sync with table selection changes."""
        self._attach_tool_scope_sync(plot_widget, on_finished_signal=plot_widget.destroyed)

    def _iter_active_plot_selection_views(self) -> list:
        """Plot surfaces that mirror table row selection (dock, floating plotter, PCA/t-SNE)."""
        from ..dockable_plot import iter_plot_selection_views

        views: list = []
        docked = getattr(self, "_docked_plot_widget", None)
        if docked is not None:
            views.extend(iter_plot_selection_views(docked))
        for plot_dlg in self._iter_plot_dialogs():
            pw = getattr(plot_dlg, "_plot_widget", None)
            if pw is not None:
                views.extend(iter_plot_selection_views(pw))
        for attr in (
            "_pca_dialog",
            "_tsne_dialog",
            "_umap_dialog",
            "_boiled_egg_dialog",
            "_golden_triangle_dialog",
        ):
            dlg = getattr(self, attr, None)
            if dlg is None:
                continue
            panel = getattr(dlg, "_panel", None)
            if panel is not None:
                views.extend(iter_plot_selection_views(panel))
                continue
            views.extend(iter_plot_selection_views(dlg))
        return views

    def _refresh_active_plot_axis_columns(self) -> None:
        """Update plotter axis dropdowns when table columns or numeric bounds change."""
        for view in self._iter_active_plot_selection_views():
            refresh = getattr(view, "refresh_axis_columns", None)
            if callable(refresh):
                try:
                    refresh()
                except RuntimeError:
                    pass

    def _sync_active_plots_from_table_selection(self) -> None:
        for view in self._iter_active_plot_selection_views():
            try:
                sync = getattr(view, "sync_from_table_selection", None)
                if callable(sync):
                    sync()
            except RuntimeError:
                pass

    def _schedule_sync_active_plots_from_table_selection(self) -> None:
        timer = getattr(self, "_plot_table_sync_timer", None)
        if timer is None:
            return
        timer.start(40)

    def _prune_plot_dialogs(self) -> None:
        alive: list = []
        for dlg in getattr(self, "_plot_dialogs", []):
            try:
                dlg.isVisible()
                alive.append(dlg)
            except RuntimeError:
                pass
        self._plot_dialogs = alive

    def _iter_plot_dialogs(self) -> list:
        self._prune_plot_dialogs()
        return list(self._plot_dialogs)

    def _register_plot_dialog(self, dlg) -> None:
        """Track a floating plotter window (multiple instances allowed)."""
        if not hasattr(self, "_plot_dialogs"):
            self._plot_dialogs = []
        self._prune_plot_dialogs()
        self._plot_dialogs.append(dlg)
        n = len(self._plot_dialogs)
        dlg.setWindowTitle("Plot Data" if n == 1 else f"Plot Data ({n})")
        dlg.destroyed.connect(lambda *_a, d=dlg: self._unregister_plot_dialog(d))

    def _unregister_plot_dialog(self, dlg) -> None:
        try:
            self._plot_dialogs.remove(dlg)
        except (ValueError, AttributeError):
            pass
        self._prune_plot_dialogs()

    def _create_plot_dialog(self):
        from ..plot import PlotDialog

        d = PlotDialog(self)
        self._prepare_tool_dialog(d)
        return d

    def _attach_tool_scope_sync(self, target, *, on_finished_signal) -> None:
        """Wire table selection changes to a dialog/plot ``only_selected_cb`` until teardown."""
        if getattr(target, "only_selected_cb", None) is None:
            return
        prior = getattr(target, "_scope_sync_disconnect", None)
        if callable(prior):
            prior()
        self._sync_dialog_only_selected_scope(target)
        sm = self.table.selectionModel()
        if sm is None:
            return

        def on_sel_changed(*_args):
            self._sync_dialog_only_selected_scope(target)

        sm.selectionChanged.connect(on_sel_changed)

        def teardown(*_args):
            try:
                from PyQt5 import sip

                if sm is not None and not sip.isdeleted(sm):
                    sm.selectionChanged.disconnect(on_sel_changed)
            except (TypeError, RuntimeError):
                pass
            target._scope_sync_disconnect = None

        on_finished_signal.connect(teardown)
        target._scope_sync_disconnect = teardown

    def dock_plot_widget(self, plot_widget) -> bool:
        """Move a plot widget into the main-window panel beside the table."""
        from ..dockable_plot import is_dockable_plot_widget
        from ..plot import PlotWidget

        if not is_dockable_plot_widget(plot_widget) and not isinstance(plot_widget, PlotWidget):
            return False
        existing = getattr(self, "_docked_plot_widget", None)
        if existing is not None and existing is not plot_widget:
            try:
                QMessageBox.information(
                    self,
                    "Plot",
                    "A plot is already docked in the main window. Close it from the plot panel first.",
                )
            except RuntimeError:
                self._docked_plot_widget = None
            else:
                return False

        host = self._plot_panel_host
        lay = host.layout()
        if lay is None:
            lay = QVBoxLayout(host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
        else:
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
        lay.addWidget(plot_widget, 1)
        self._docked_plot_widget = plot_widget
        self._plot_panel.setVisible(True)
        prior_teardown = getattr(plot_widget, "_scope_sync_disconnect", None)
        if callable(prior_teardown):
            prior_teardown()
        self._prepare_tool_plot(plot_widget)
        plot_widget.destroyed.connect(self._on_docked_plot_destroyed)
        self.status_label.setText("Plot: docked to the right of the table.")
        return True

    def _on_docked_plot_destroyed(self, *_args) -> None:
        self._docked_plot_widget = None
        try:
            self._plot_panel.setVisible(False)
        except Exception:
            pass

    def close_plot_panel_keep_plot(self) -> None:
        """Hide the docked plot panel; the plot widget and its state are preserved."""
        self._plot_panel.setVisible(False)
        self.status_label.setText("Plot panel hidden.")

    def _release_plot_widget_from_panel_host(self, plot_widget) -> None:
        host = self._plot_panel_host
        lay = host.layout()
        if lay is not None:
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
        teardown = getattr(plot_widget, "_scope_sync_disconnect", None)
        if callable(teardown):
            teardown()
        self._docked_plot_widget = None

    def undock_plot_to_window(self) -> bool:
        """Move the docked plot from the main window into a floating window."""
        from ..plot import PlotDialog, PlotWidget

        plot_widget = getattr(self, "_docked_plot_widget", None)
        if plot_widget is None:
            return False

        factory = getattr(plot_widget, "create_floating_dialog", None)
        if callable(factory):
            self._release_plot_widget_from_panel_host(plot_widget)
            self._plot_panel.setVisible(False)
            dlg = factory(self)
            self._prepare_tool_dialog(dlg)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            self.status_label.setText("Plot: moved to separate window.")
            return True

        if not isinstance(plot_widget, PlotWidget):
            self._docked_plot_widget = None
            return False

        self._release_plot_widget_from_panel_host(plot_widget)
        self._plot_panel.setVisible(False)

        dlg = PlotDialog(self, plot_widget=plot_widget)
        self._register_plot_dialog(dlg)
        self._prepare_tool_dialog(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self.status_label.setText("Plot: moved to separate window.")
        return True

    def toggle_plot_panel(self) -> None:
        """Show/hide the docked plot panel, or open the floating plotter if none is docked."""
        w = getattr(self, "_docked_plot_widget", None)
        if w is None:
            self.open_plot()
            return
        try:
            visible = self._plot_panel.isVisible()
            self._plot_panel.setVisible(not visible)
            if not visible:
                self._sync_dialog_only_selected_scope(w)
        except RuntimeError:
            self._docked_plot_widget = None
            self.open_plot()

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

    def _on_structure_source_probe(self, headers: list) -> None:
        """Worker paused after first record; pick structure column before bulk read."""
        incoming = list(headers)
        if self._ingest_append_mode and self._table_model.rowCount() > 0:
            self._merge_import_headers(incoming)
        else:
            self.headers = incoming
        from ...import_structure import structure_source_picker_candidates
        from ..dialogs.structure_source import StructureSourcePickerDialog

        struct_cols = structure_source_picker_candidates(self.headers)
        if len(struct_cols) >= 2:
            picked, ok = StructureSourcePickerDialog.pick_column(self, struct_cols)
            if ok and picked:
                self._structure_field_override = picked
            elif not ok:
                self._structure_field_override = None
        ev = getattr(self, "_structure_choice_event", None)
        if ev is not None:
            ev.set()

    def _maybe_start_ingest_processing(self) -> None:
        if self._processing_batches:
            return
        if not self._pending_batches and not self._last_batch_received:
            return
        self._processing_batches = True
        QTimer.singleShot(0, self._process_next_chunk)

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
            if self._table_stack.currentIndex() == 0:
                self._loading_detail.setText(LOADING_DETAIL_AFTER_FILE_READ)
        if mols_list:
            self._pending_batches.append((mols_list, is_last))
        if is_last:
            self._last_batch_received = True
        self._maybe_start_ingest_processing()

    def _process_next_chunk(self, chunk_size: int | None = None):
        if chunk_size is None:
            chunk_size = int(load_config().ingest_gui_chunk_size)
        # Process up to chunk_size molecules, then reschedule to keep UI responsive
        processed = 0
        perf = getattr(self, "_perf", None)
        scope = perf.track if perf is not None else (lambda *_args, **_kwargs: nullcontext())
        with scope("ingest.process_chunk"):
            while self._pending_batches and processed < chunk_size:
                mols_list, is_last = self._pending_batches.pop(0)
                remain = max(0, int(chunk_size - processed))
                take = mols_list[:remain]
                if take:
                    new_rows: list[tuple[int, dict[str, str]]] = []
                    for m in take:
                        m = self._apply_structure_field_override(m)
                        oid = self.next_oid
                        self.next_oid += 1
                        self.mols[oid] = m
                        new_rows.append((oid, self._row_cells_from_mol(m)))
                    self._table_model.append_rows_batch(new_rows)
                    processed += len(new_rows)
                    n = len(self.mols)
                    self.status_label.setText(f"Loaded {n} molecules — preparing table…")
                    if self._table_stack.currentIndex() == 0:
                        self._loading_detail.setText(f"Building table…\n{n} molecule(s); 2D structures draw when ready")
                    if getattr(self, "_ingest_loading", False):
                        if not self._import_building_progress_shown:
                            self._import_building_progress_shown = True
                            self._on_tool_progress("Building table…", -1, -1)
                    del mols_list[: len(take)]
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
                if getattr(self, "_sqlite_store", None) is not None:
                    self._sqlite_store_dirty = True
                else:
                    self._rebuild_sqlite_store_from_model()
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

    def _rebuild_sqlite_store_from_model(self) -> None:
        """Synchronous rebuild (clear table, tests). Large tables use ``_schedule_sqlite_rebuild``."""
        store = getattr(self, "_sqlite_store", None)
        if store is None:
            return
        self._sqlite_rebuild_in_progress = True
        data_headers = [h for h in self.headers[2:] if h and not self._table_model.is_pixmap_data_column(h)]
        entries = self._table_model.export_rows_for_sqlite(data_headers)
        perf = getattr(self, "_perf", None)
        scope = perf.track if perf is not None else (lambda *_args, **_kwargs: nullcontext())
        try:
            with scope("sqlite.rebuild"):
                store.rebuild(self.headers, entries)
            self._sqlite_store_dirty = False
        finally:
            self._sqlite_rebuild_in_progress = False

    def _schedule_sqlite_rebuild(self) -> None:
        """Export on the UI thread, rebuild SQLite mirror in a background worker."""
        store = getattr(self, "_sqlite_store", None)
        if store is None or self._sqlite_rebuild_in_progress:
            return
        sigs = getattr(self, "_sqlite_rebuild_signals", None)
        pool = getattr(self, "threadpool", None)
        if sigs is None or pool is None:
            self._rebuild_sqlite_store_from_model()
            return
        self._sqlite_rebuild_gen = int(getattr(self, "_sqlite_rebuild_gen", 0)) + 1
        gen = self._sqlite_rebuild_gen
        self._sqlite_rebuild_in_progress = True
        data_headers = [h for h in self.headers[2:] if h and not self._table_model.is_pixmap_data_column(h)]
        entries = self._table_model.export_rows_for_sqlite(data_headers)
        import os
        import tempfile
        from pathlib import Path

        fd, db_path = tempfile.mkstemp(prefix="MOLMANAGER_sqlite_", suffix=".sqlite3")
        try:
            os.close(fd)
        except OSError:
            pass
        self._sqlite_rebuild_pending_path = Path(db_path)
        n = len(entries)
        self.status_label.setText(f"Indexing table… ({n} rows)")
        pool.start(
            SqliteRebuildWorker(gen, list(self.headers), entries, db_path, sigs),
        )

    def _on_sqlite_rebuild_finished(self, job_gen: int, db_path: str) -> None:
        if job_gen != getattr(self, "_sqlite_rebuild_gen", -1):
            return
        try:
            new_store = SqliteTableStore(db_path)
            old = getattr(self, "_sqlite_store", None)
            self._sqlite_store = new_store
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            self._sqlite_store_dirty = False
        except Exception:
            logger.exception("Failed to swap SQLite row store after background rebuild")
        finally:
            self._sqlite_rebuild_in_progress = False
            self._sqlite_rebuild_pending_path = None
        if getattr(self, "_sqlite_rebuild_stale", False):
            self._sqlite_rebuild_stale = False
            self._sqlite_store_dirty = True
            self._schedule_sqlite_rebuild()
            return
        n_rows = self._table_model.rowCount()
        if getattr(self, "_sqlite_rebuild_pending_filters", False):
            self._sqlite_rebuild_pending_filters = False
            self.apply_filters()
        elif n_rows:
            self.status_label.setText(loaded_session_status(n_rows))

    def _on_sqlite_rebuild_failed(self, job_gen: int, msg: str) -> None:
        if job_gen != getattr(self, "_sqlite_rebuild_gen", -1):
            return
        logger.warning("SQLite rebuild failed: %s", msg)
        self._sqlite_rebuild_in_progress = False
        self._sqlite_rebuild_pending_path = None
        self._sqlite_rebuild_pending_filters = False
        self.status_label.setText("Table indexing failed — filters may be slow until data changes.")

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
                    if getattr(self, "_render2d_column_pixmap_mode", True):
                        self._table_model.register_pixmap_column(pix_target)
                        self._table_model.set_column_pixmap(oid, pix_target, pm)
                    else:
                        self._table_model.set_cell_pixmap(oid, pix_target, pm)
                else:
                    self._table_model.set_structure_pixmap(oid, pm)
                if self.table.rowHeight(row) != h:
                    self.table.setRowHeight(row, h)
                if pix_target:
                    self._sync_data_pixmap_column_width(pix_target, pm, w)
                else:
                    self._sync_structure_column_width_for_pixmap(pm, w)
                if props:
                    updates = {name: str(props.get(name, "")) for name in self.headers[2:] if name in props}
                    if updates:
                        self._table_model.set_cell_text_batch(oid, updates)
                pend = getattr(self, "_structure_column_autosize_after_render_oid", None)
                if pend is not None and pend == oid:
                    self._structure_column_autosize_after_render_oid = None
                    if self._table_model.rowCount() <= 20_000:
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

    def _resize_columns_after_render2d(self, pix_target: str | None) -> None:
        """Set structure / pixmap column width from depict size (O(1), safe for huge tables)."""
        pad = STRUCTURE_COLUMN_HORIZONTAL_PADDING
        need = max(1, int(STRUCTURE_DEPICT_WIDTH) + pad)
        try:
            if pix_target:
                col = self.headers.index(pix_target)
                if self.table.columnWidth(col) < need:
                    self.table.setColumnWidth(col, need)
            else:
                self._sync_structure_column_width_for_pixmap(None, STRUCTURE_DEPICT_WIDTH)
        except Exception:
            pass

    def _render2d_use_lazy_structure_flush(self, count: int, *, structure_column: bool) -> bool:
        if not structure_column:
            return False
        cfg = load_config()
        return count >= int(cfg.structure_render_lazy_min_rows)

    def _ensure_structure_lazy_scroll_hook(self) -> None:
        if getattr(self, "_structure_lazy_scroll_hooked", False):
            return
        try:
            self.table.verticalScrollBar().valueChanged.connect(self._on_structure_lazy_scroll)
            self._structure_lazy_scroll_hooked = True
        except Exception:
            pass

    def _on_structure_lazy_scroll(self, *_args) -> None:
        if not self._table_model.structure_png_store_active():
            return
        self._refresh_visible_structure_cells()

    def _refresh_visible_structure_cells(self) -> None:
        """Repaint only viewport-visible Structure cells (lazy PNG cache)."""
        m = self._table_model
        if m.rowCount() <= 0:
            return
        view = self.table
        try:
            r0 = view.rowAt(0)
            r1 = view.rowAt(max(0, view.viewport().height() - 1))
        except Exception:
            r0, r1 = 0, m.rowCount() - 1
        if r0 < 0:
            r0 = 0
        if r1 < 0:
            r1 = m.rowCount() - 1
        store = getattr(m, "_structure_png_store", None)
        if store is not None:
            keep: set[int] = set()
            for r in range(r0, r1 + 1):
                oid = m.row_oid(r)
                if store.has_png(oid):
                    keep.add(int(oid))
            store.trim_decoded_cache(keep_oids=keep)
        m.notify_structure_column_changed(r0, r1)

    def _flush_render2d_batch_results(self) -> None:
        """Apply buffered PNGs in one pass (table stayed on placeholders until workers finished)."""
        if not getattr(self, "_render2d_batch_active", False):
            return
        self._render2d_accept_session = None
        ordered = list(getattr(self, "_render2d_batch_oids_ordered", []) or [])
        pending = getattr(self, "_render2d_pending", None) or {}
        pix_target = getattr(self, "_render2d_pixmap_target", None)
        column_pixmap_mode = getattr(self, "_render2d_column_pixmap_mode", True)
        lazy_structure = bool(getattr(self, "_render2d_lazy_flush", False)) and not pix_target
        if pix_target and column_pixmap_mode:
            self._table_model.register_pixmap_column(pix_target)
        set_pixmap = (
            self._table_model.set_column_pixmap
            if column_pixmap_mode
            else self._table_model.set_cell_pixmap
        )
        try:
            self.table.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            if lazy_structure:
                cfg = load_config()
                store = StructureRenderStore(max_decoded_pixmaps=cfg.structure_render_pixmap_lru)
                png_items: list[tuple[int, bytes]] = []
                for oid in ordered:
                    rec = pending.get(oid)
                    if not rec:
                        continue
                    img_b, ok, _rw, _rh = rec
                    if ok and img_b:
                        png_items.append((int(oid), bytes(img_b)))
                if png_items:
                    store.ingest_batch(png_items)
                self._table_model.set_structure_png_store(store)
                self._ensure_structure_lazy_scroll_hook()
                self._refresh_visible_structure_cells()
            else:
                eager: list[tuple[int, QPixmap | None]] = []
                default_rh = STRUCTURE_ROW_DEFAULT_HEIGHT
                set_uniform_height = len(ordered) >= load_config().structure_render_lazy_min_rows
                for oid in ordered:
                    row = self._resolve_structure_row_for_oid(int(oid))
                    rec = pending.get(oid)
                    if not rec:
                        if pix_target:
                            set_pixmap(oid, pix_target, None)
                        else:
                            eager.append((int(oid), None))
                        continue
                    img_b, ok, rw, rh = rec
                    if not ok or row < 0:
                        if pix_target:
                            set_pixmap(oid, pix_target, None)
                        else:
                            eager.append((int(oid), None))
                        continue
                    pm = QPixmap.fromImage(QImage.fromData(img_b))
                    if pix_target:
                        set_pixmap(oid, pix_target, pm)
                    else:
                        eager.append((int(oid), pm))
                    if row >= 0 and not set_uniform_height and self.table.rowHeight(row) != rh:
                        self.table.setRowHeight(row, rh)
                if eager and not pix_target:
                    self._table_model.apply_structure_pixmaps_batch(eager, emit=True)
                elif eager and pix_target:
                    pass
                if set_uniform_height and not pix_target:
                    vh = self.table.verticalHeader()
                    try:
                        vh.setSectionResizeMode(vh.Fixed)
                    except Exception:
                        pass
                    for row in range(self._table_model.rowCount()):
                        if self.table.rowHeight(row) != default_rh:
                            self.table.setRowHeight(row, default_rh)
        finally:
            try:
                self.table.setUpdatesEnabled(True)
            except Exception:
                pass
        self._render2d_pending = {}
        self._render2d_batch_oids_ordered = []
        self._render2d_snapshot = None
        self._render2d_lazy_flush = False

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
        candidates = self.chemistry_tool_structure_sources()
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
                    mol = self._mol_for_structure_row(r)
                if mol is None:
                    continue
                self.mols[oid] = mol
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
        n_rows = self._table_model.rowCount()
        cfg = load_config()
        max_auto = int(cfg.auto_render_2d_max_rows)
        if max_auto > 0 and n_rows > max_auto:
            self.status_label.setText(
                f"Loaded {n_rows:,} rows — auto 2D render skipped (limit {max_auto:,}). "
                "Use Tools → Render 2D for visible or selected rows."
            )
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
        self._render2d_column_pixmap_mode = True
        self._resize_columns_after_render2d(pix_target)
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
        done_ev = getattr(self, "_render2d_batch_done_event", None)
        if done_ev is not None:
            done_ev.set()
        pq = getattr(self, "process_queue", None)
        if pq is not None:
            pq.schedule_resume()

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
        if getattr(self, "_render2d_lazy_flush", False) and not getattr(self, "_render2d_pixmap_target", None):
            self._table_model.clear_structure_png_store()
        snap = getattr(self, "_render2d_snapshot", None)
        target = getattr(self, "_render2d_pixmap_target", None)
        if snap:
            column_pixmap_mode = getattr(self, "_render2d_column_pixmap_mode", True)
            set_pixmap = (
                self._table_model.set_column_pixmap
                if column_pixmap_mode
                else self._table_model.set_cell_pixmap
            )
            for oid, pm in snap.items():
                if target:
                    set_pixmap(oid, target, pm)
                else:
                    self._table_model.set_structure_pixmap(oid, pm)
        self._render2d_snapshot = None
        self._render2d_lazy_flush = False
        self._import_progress_active = False
        self._clear_tool_progress()
        self._restore_render2d_batch_environment()
        return True

    def run_render_2d_structures(self) -> None:
        """Queue 2D structure renders for all rows (after deferred load)."""
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(self, TOOL_RENDER_2D, "Load a table with at least one row first.")
            return
        if self._render2d_batch_active or self.process_queue.has_running_job():
            QMessageBox.warning(
                self,
                TOOL_RENDER_2D,
                "A render or background tool is already running. Wait for it to finish or cancel it from Processes.",
            )
            return
        candidates = self.chemistry_tool_structure_sources()
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
        self._start_render_2d_batch(renders, row_by_oid, src, column_pixmap_mode=(src != "Structure"))

    def _start_render_2d_batch(
        self,
        renders,
        row_by_oid,
        src: str = "Structure",
        *,
        column_pixmap_mode: bool = True,
    ) -> None:
        """Queue batch 2D renders on the serial process queue (waits behind other tools)."""
        if getattr(self, "_render2d_batch_active", False):
            return
        title = f"Render 2D ({len(renders)} rows)"
        payload = (renders, row_by_oid, src, column_pixmap_mode)
        self.process_queue.enqueue(
            title,
            lambda ev, p=payload: Render2DBatchHeldJob(self, p, ev),
        )

    def _begin_render2d_batch_impl(
        self,
        renders,
        row_by_oid,
        src: str = "Structure",
        *,
        column_pixmap_mode: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Start batch 2D rendering on the GUI thread (called from :class:`Render2DBatchHeldJob`)."""
        if cancel_event is not None and cancel_event.is_set():
            return
        self._render2d_session_id += 1
        self._render2d_batch_session_tag = self._render2d_session_id
        self._render2d_accept_session = self._render2d_batch_session_tag
        self._render2d_pixmap_target = None if src == "Structure" else src
        self._render2d_column_pixmap_mode = bool(column_pixmap_mode)
        if self._render2d_pixmap_target and self._render2d_column_pixmap_mode:
            self._table_model.register_pixmap_column(self._render2d_pixmap_target)
        self._render2d_saved_sort_enabled = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self._render2d_batch_active = True
        self._render2d_row_by_oid = row_by_oid
        oids = [oid for oid, _, _, _ in renders]
        self._render2d_batch_oids_ordered = oids
        self._render2d_pending = {}
        structure_column = self._render2d_pixmap_target is None
        lazy_structure = self._render2d_use_lazy_structure_flush(len(oids), structure_column=structure_column)
        self._render2d_lazy_flush = lazy_structure
        tgt = self._render2d_pixmap_target
        if tgt:
            if self._render2d_column_pixmap_mode:
                self._render2d_snapshot = self._table_model.snapshot_column_pixmaps(tgt, oids)
                clear_pixmap = self._table_model.set_column_pixmap
            else:
                self._render2d_snapshot = {
                    oid: self._table_model.cell_pixmap_copy(oid, tgt) for oid in oids
                }
                clear_pixmap = self._table_model.set_cell_pixmap
            for oid in oids:
                clear_pixmap(oid, tgt, None)
        elif lazy_structure:
            self._render2d_snapshot = {}
            self._table_model.clear_structure_png_store()
            self._table_model.clear_structure_pixmaps_for_oids(oids, emit=True)
        else:
            if len(oids) <= load_config().structure_render_lazy_min_rows:
                self._render2d_snapshot = self._table_model.snapshot_structure_pixmaps(oids)
            else:
                self._render2d_snapshot = {}
            self._table_model.clear_structure_pixmaps_for_oids(oids, emit=True)
        self._render2d_progress_last_emit = 0.0
        self._render2d_progress_last_done = 0
        self._import_progress_active = True
        self._import_render_goal = len(renders)
        self._import_render_done = 0
        self._on_tool_progress("Drawing 2D structures…", 0, len(renders))
        self._render2d_cancel_event = cancel_event if cancel_event is not None else threading.Event()
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

    def _render2d_source_header_for_column(self, col: int) -> str:
        """Header used as Render 2D source and pixmap target (clicked column, else Structure)."""
        if 0 <= col < len(self.headers):
            return self.headers[col]
        return "Structure"

    def _mol_for_render2d_source(self, row: int, src: str) -> Chem.Mol | None:
        """Molecule to draw for one row from the chosen source column."""
        if row < 0 or row >= self._table_model.rowCount():
            return None
        if src == "Structure":
            return self._mol_for_structure_row(row)
        if src not in self.headers:
            return None
        ci = self.headers.index(src)
        if self._table_model.is_pixmap_data_column(src):
            raw = (self._table_model.backing_value_for_row_header(row, src) or "").strip()
            mol = self._mol_from_structure_text(raw) if raw else None
            if mol is not None:
                return mol
            return self._mol_for_structure_row(row)
        raw = (self._table_cell_text(row, ci) or "").strip()
        if not raw:
            raw = (self._table_model.backing_value_for_row_header(row, src) or "").strip()
        if not raw:
            return None
        return self._mol_from_structure_text(raw)

    def run_render_2d_for_table_row(self, row: int, col: int | None = None) -> None:
        """Run Render 2D for one row: read chemistry from ``col`` and write the pixmap into that column."""
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(self, TOOL_RENDER_2D, "Load a table with at least one row first.")
            return
        if self._render2d_batch_active or self.process_queue.has_running_job():
            QMessageBox.warning(
                self,
                TOOL_RENDER_2D,
                "A render or background tool is already running. Wait for it to finish or cancel it from Processes.",
            )
            return
        if row < 0 or row >= self._table_model.rowCount():
            return
        t0 = self._table_model.cell_text(row, 0)
        oid = int(t0) if t0.isdigit() else None
        if oid is None:
            QMessageBox.information(self, TOOL_RENDER_2D, "Could not resolve this row’s compound id.")
            return
        src = self._render2d_source_header_for_column(col if col is not None else -1)
        mol = self._mol_for_render2d_source(row, src)
        if mol is None:
            QMessageBox.information(
                self,
                TOOL_RENDER_2D,
                f"No structure could be read from column “{src}” for this row.",
            )
            return
        self.mols[oid] = mol
        base_w, base_h = STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT
        renders, row_by_oid = self._build_render2d_tasks_in_table_order(src, base_w, base_h, {oid})
        if not renders:
            QMessageBox.information(
                self,
                TOOL_RENDER_2D,
                f"No structure could be read from column “{src}” for this row.",
            )
            return
        self._start_render_2d_batch(renders, row_by_oid, src, column_pixmap_mode=False)

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
        self.status_label.setText(self._consume_partial_results_notice() or "Done.")

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

    def _collect_mols_for_conformer_tools(
        self, *, only_selected: bool
    ) -> list[tuple[int, Chem.Mol]]:
        allowed = self._selected_oids_set() if only_selected else None
        oids_list = self._all_oids_in_table_order()
        if allowed is not None:
            oids_list = [o for o in oids_list if o in allowed]
        data: list[tuple[int, Chem.Mol]] = []
        for o in oids_list:
            r = self.get_row_by_id(o)
            m = self.mols.get(o) if r >= 0 else None
            if m is None and r >= 0:
                m = self._mol_for_structure_row(r)
            if m is not None:
                data.append((o, m))
        return data

    def _on_generate_conformations_dialog_accepted(self, d) -> None:
        only_selected = d.only_selected_rows()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, "Generate Conformations"):
            return
        data = self._collect_mols_for_conformer_tools(only_selected=only_selected)
        if not data:
            QMessageBox.information(
                self,
                "Generate Conformations",
                "No parseable structures for those rows (in-memory molecules or chemistry in table cells).",
            )
            return
        params = d.params()
        n = len(data)
        ps = self._tool_progress_state
        self._begin_tool_progress("Generate conformations", n)
        self.process_queue.enqueue(
            f"Generate conformations ({n} structures)",
            lambda ev, d=data, p=params, sigs=self.signals, prog=ps: ConformerGenerationWorker(
                d, p, sigs, cancel_event=ev, progress_state=prog
            ),
        )

    def open_generate_single_conformation(self) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                TOOL_SINGLE_CONFORMATION,
                "Open a file or add rows so the table has molecules to process.",
            )
            return
        from ..dialogs import GenerateSingleConformationDialog

        d = GenerateSingleConformationDialog(len(self._selected_logical_rows()), self)
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(
            lambda *_, dlg=d: self._on_generate_single_conformation_dialog_accepted(dlg)
        )
        d.show()

    def _on_generate_single_conformation_dialog_accepted(self, d) -> None:
        only_selected = d.only_selected_rows()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, TOOL_SINGLE_CONFORMATION):
            return
        data = self._collect_mols_for_conformer_tools(only_selected=only_selected)
        if not data:
            QMessageBox.information(
                self,
                TOOL_SINGLE_CONFORMATION,
                "No parseable structures for those rows (in-memory molecules or chemistry in table cells).",
            )
            return
        params = d.params()
        n = len(data)
        ps = self._tool_progress_state
        self._begin_tool_progress(TOOL_SINGLE_CONFORMATION, n)
        self.process_queue.enqueue(
            f"{TOOL_SINGLE_CONFORMATION} ({n} structures)",
            lambda ev, d=data, p=params, sigs=self.signals, prog=ps: ConformerGenerationWorker(
                d, p, sigs, cancel_event=ev, progress_state=prog
            ),
        )

    def cancel_active_tool_process(self) -> None:
        """Request cooperative cancellation of the process-queue job, Render 2D, Boltz-2, and/or Vina."""
        r2d = self.cancel_render_2d_batch()
        boltz = self.cancel_boltz2_predict()
        vina = self.cancel_vina_dock()
        pq_ok = self.process_queue.cancel_running()
        if pq_ok:
            self.status_label.setText("Cancelling…")
        elif r2d:
            self.status_label.setText("Render 2D cancelled.")
        elif boltz:
            self.status_label.setText("Boltz-2 stopped.")
        elif vina:
            self.status_label.setText("Vina stopped.")
        else:
            QMessageBox.information(
                self,
                "Cancel Process",
                "Nothing to cancel (no process-queue job, Render 2D batch, Boltz-2 run, or Vina run), "
                "or cancellation was already requested.",
            )

    def on_conformers_finished(self, results: list) -> None:
        self._finish_tool_progress("Generate conformations")
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
        self.status_label.setText(self._consume_partial_results_notice() or "Done.")

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
        ps = self._tool_progress_state
        self._begin_tool_progress("Superpose conformers", n)
        self.process_queue.enqueue(
            f"Superpose conformers ({n} rows)",
            lambda ev, d=data, p=params, sigs=self.signals, prog=ps: SuperposeConformersWorker(
                d, p, sigs, cancel_event=ev, progress_state=prog
            ),
        )

    def on_superpose_finished(self, results: list) -> None:
        self._finish_tool_progress("Superpose conformers")
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
        self.status_label.setText(self._consume_partial_results_notice() or "Done.")

    def open_calc(self):
        if not self.headers:
            return
        from ..dialogs import PropertyDialog

        desc_src_cols = self.chemistry_tool_structure_sources()
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
        ps = self._tool_progress_state
        self._begin_tool_progress("Calculate descriptors", len(data))
        self.process_queue.enqueue(
            f"Calculate descriptors ({len(data)} rows)",
            lambda ev, d=data, dh=disp, fn=fns, sm=is_s, sigs=self.signals, p=ps: CalcWorker(
                d, dh, fn, sm, sigs, cancel_event=ev, progress_state=p
            ),
        )

    def _sync_global_bounds_for_headers(self, headers: list[str], *, refresh_filters: bool = False) -> None:
        """Refresh slider min/max for specific columns without scanning the whole table."""
        if not headers:
            return
        self._table_model.refresh_numeric_bounds_for_headers(headers)
        cache = self._table_model._numeric_bounds_cache
        if cache is not None:
            for h in headers:
                if h in cache:
                    self.global_bounds[h] = cache[h]
                else:
                    self.global_bounds.pop(h, None)
        if refresh_filters:
            cols = self._filterable_data_column_names()
            for f in self.filters:
                if isinstance(f, FilterCard):
                    f.update_prop_list(list(self.global_bounds.keys()))
                elif isinstance(f, (TextFilterCard, CategoryFilterCard)):
                    f.update_prop_list(cols)
        self._refresh_active_plot_axis_columns()

    def on_calc_finished(self, res, calc_h, *, finish_progress: bool = True, progress_label: str | None = None):
        if finish_progress:
            self._finish_tool_progress(progress_label)
        self.table.setSortingEnabled(False)
        try:
            self.table.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            h_map = {h: i for i, h in enumerate(self.headers)}
            new_h = [h for h in calc_h if h not in h_map]
            if new_h:
                col_at = len(self.headers)
                self.headers.extend(new_h)
                self._table_model.insert_columns_at(col_at, new_h, None)
            bulk_rows = [(int(oid), {h: str(row_d.get(h, "N/A")) for h in calc_h}) for oid, row_d in res]
            if bulk_rows:
                if len(calc_h) == 1:
                    hdr = calc_h[0]
                    self._table_model.set_column_text_by_oids(
                        hdr,
                        [(oid, values[hdr]) for oid, values in bulk_rows],
                    )
                else:
                    self._table_model.apply_columns_values_bulk(calc_h, bulk_rows)
            self._sync_global_bounds_for_headers(calc_h, refresh_filters=bool(new_h))
            self.table.setSortingEnabled(False)
        finally:
            try:
                self.table.setUpdatesEnabled(True)
            except Exception:
                pass
        self.status_label.setText(self._consume_partial_results_notice() or "Done.")

    def open_core_based_decomposition(self) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                TOOL_CORE_DECOMP,
                "Load a table with at least one row first.",
            )
            return
        candidates = self.chemistry_tool_structure_sources()
        from ..dialogs import CoreBasedDecompositionDialog

        d = CoreBasedDecompositionDialog(candidates, len(self._selected_logical_rows()), self)
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(lambda *_, dlg=d: self._on_core_based_decomposition_dialog_accepted(dlg))
        d.show()

    def _on_core_based_decomposition_dialog_accepted(self, d) -> None:
        p = d.params()
        only_selected = d.only_selected_rows()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, TOOL_CORE_DECOMP):
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
                TOOL_CORE_DECOMP,
                "No valid structures were found for the selected source and scope.",
            )
            self.status_label.setText("Ready.")
            return
        ps = self._tool_progress_state
        self._begin_tool_progress("Core-based decomposition", len(data))
        self.process_queue.enqueue(
            f"Core-based decomposition ({len(data)} rows)",
            lambda ev, dt=data, pp=p, sigs=self.signals, prog=ps: RGroupDecompositionWorker(
                dt,
                pp.core_query,
                pp.column_prefix,
                pp.only_match_at_r_groups,
                pp.remove_hydrogens_post_match,
                pp.matching,
                sigs,
                cancel_event=ev,
                progress_state=prog,
            ),
        )

    def on_rgroup_decomp_finished(self, res, col_headers: list) -> None:
        self.on_calc_finished(res, col_headers, progress_label=TOOL_CORE_DECOMP)

    def on_rgroup_decomp_failed(self, message: str) -> None:
        self._clear_tool_progress()
        self.status_label.setText("Ready.")
        QMessageBox.warning(self, TOOL_CORE_DECOMP, message or "Core-based decomposition failed.")

    def open_brics_decomposition(self) -> None:
        self._open_fragment_decomposition_dialog(
            method="brics",
            window_title=TOOL_BRICS_DECOMP,
            default_prefix="BRICS",
            intro=(
                "Break each structure into BRICS fragments (retrosynthetic building blocks). "
                "Fragments are written as SMILES in new columns (BRICS_1, BRICS_2, …). "
                "Dummy atoms in SMILES mark connection points."
            ),
        )

    def open_recap_decomposition(self) -> None:
        self._open_fragment_decomposition_dialog(
            method="recap",
            window_title=TOOL_RECAP_DECOMP,
            default_prefix="RECAP",
            intro=(
                "Break each structure into RECAP fragments (retrosynthetic hierarchies). "
                "Leaf fragments are written as SMILES in new columns (RECAP_1, RECAP_2, …)."
            ),
        )

    def _open_fragment_decomposition_dialog(
        self,
        *,
        method: str,
        window_title: str,
        default_prefix: str,
        intro: str,
    ) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                window_title,
                "Load a table with at least one row first.",
            )
            return
        from ..dialogs import FragmentDecompositionDialog

        d = FragmentDecompositionDialog(
            window_title=window_title,
            intro=intro,
            default_prefix=default_prefix,
            method=method,
            structure_sources=self.chemistry_tool_structure_sources(),
            selected_row_count=len(self._selected_logical_rows()),
            parent=self,
        )
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(
            lambda *_, dlg=d: self._on_fragment_decomposition_dialog_accepted(dlg)
        )
        d.show()

    def _on_fragment_decomposition_dialog_accepted(self, d) -> None:
        p = d.params()
        only_selected = d.only_selected_rows()
        if self._abort_if_only_selected_but_empty(only_selected, self._selected_oids_set(), p.tool_title):
            return
        data = self.collect_scoped_table_mols(p.structure_source, only_selected=only_selected)
        if not data:
            QMessageBox.information(
                self,
                p.tool_title,
                "No valid structures were found for the selected source and scope.",
            )
            self.status_label.setText("Ready.")
            return
        prefix = p.column_prefix or ("BRICS" if p.method == "brics" else "RECAP")
        method = "brics" if p.method == "brics" else "recap"
        ps = self._tool_progress_state
        self._begin_tool_progress(p.tool_title, len(data))
        self.process_queue.enqueue(
            f"{p.tool_title} ({len(data)} rows)",
            lambda ev, dt=data, m=method, pref=prefix, title=p.tool_title, sigs=self.signals, prog=ps: FragmentDecompositionWorker(
                dt,
                m,
                pref,
                title,
                sigs,
                cancel_event=ev,
                progress_state=prog,
            ),
        )

    def on_fragment_decomp_finished(self, res, col_headers: list, tool_title: str) -> None:
        self._finish_tool_progress(tool_title)
        self.on_calc_finished(res, col_headers, finish_progress=False)

    def on_fragment_decomp_failed(self, message: str, tool_title: str) -> None:
        self._clear_tool_progress()
        self.status_label.setText("Ready.")
        QMessageBox.warning(self, tool_title, message or "Fragment decomposition failed.")

    def open_brics_recomposition(self) -> None:
        self._open_fragment_recomposition_dialog(
            method="brics",
            window_title=TOOL_BRICS_RECOMP,
            default_prefix="BRICS",
            intro=(
                "Combine unique BRICS fragments from decomposition columns (e.g. BRICS_1, BRICS_2) "
                "into new product structures using RDKit BRICSBuild. Products are appended as new "
                "table rows with SMILES and 2D structures."
            ),
        )

    def open_recap_recomposition(self) -> None:
        self._open_fragment_recomposition_dialog(
            method="recap",
            window_title=TOOL_RECAP_RECOMP,
            default_prefix="RECAP",
            intro=(
                "Combine unique RECAP fragments from decomposition columns (e.g. RECAP_1, RECAP_2) "
                "into new product structures. RECAP attachment points (*) are coupled via the "
                "BRICS builder; products are appended as new rows with SMILES and 2D structures."
            ),
        )

    def _open_fragment_recomposition_dialog(
        self,
        *,
        method: str,
        window_title: str,
        default_prefix: str,
        intro: str,
    ) -> None:
        if not self.headers or self._table_model.rowCount() == 0:
            QMessageBox.information(
                self,
                window_title,
                "Load a table with fragment columns from decomposition first.",
            )
            return
        from ..dialogs import FragmentRecompositionDialog

        d = FragmentRecompositionDialog(
            window_title=window_title,
            intro=intro,
            default_prefix=default_prefix,
            method=method,
            table_headers=list(self.headers),
            selected_row_count=len(self._selected_logical_rows()),
            parent=self,
        )
        self._prepare_tool_dialog(d)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.accepted.connect(
            lambda *_, dlg=d: self._on_fragment_recomposition_dialog_accepted(dlg)
        )
        d.show()

    def _collect_fragment_smiles_for_prefix(
        self,
        prefix: str,
        *,
        only_selected: bool,
    ) -> list[str]:
        from ...fragment_decomposition import fragment_columns_for_prefix

        cols = fragment_columns_for_prefix(self.headers, prefix)
        if not cols:
            return []
        allowed = self._selected_oids_set() if only_selected else None
        col_idx = {h: self.headers.index(h) for h in cols}
        out: list[str] = []
        for r in range(self._table_model.rowCount()):
            t0 = self._table_model.cell_text(r, 0)
            if not t0.isdigit():
                continue
            oid = int(t0)
            if allowed is not None and oid not in allowed:
                continue
            for h in cols:
                raw = self._table_cell_text(r, col_idx[h])
                if not raw:
                    raw = self._table_model.backing_value_for_row_header(r, h) or ""
                if raw:
                    out.append(str(raw).strip())
        return out

    def _on_fragment_recomposition_dialog_accepted(self, d) -> None:
        p = d.params()
        only_selected = d.only_selected_rows()
        if self._abort_if_only_selected_but_empty(
            only_selected, self._selected_oids_set(), p.tool_title
        ):
            return
        fragments = self._collect_fragment_smiles_for_prefix(
            p.column_prefix, only_selected=only_selected
        )
        if not fragments:
            QMessageBox.information(
                self,
                p.tool_title,
                f"No fragment SMILES found in columns matching “{p.column_prefix}_1”, “{p.column_prefix}_2”, …",
            )
            self.status_label.setText("Ready.")
            return
        method = "brics" if p.method == "brics" else "recap"
        ps = self._tool_progress_state
        self._begin_tool_progress(p.tool_title, 1)
        self.process_queue.enqueue(
            f"{p.tool_title} ({len(fragments)} fragments)",
            lambda ev, fr=fragments, pp=p, m=method, sigs=self.signals, prog=ps: FragmentRecompositionWorker(
                fr,
                m,
                pp.max_depth,
                pp.max_products,
                pp.tool_title,
                sigs,
                cancel_event=ev,
                progress_state=prog,
            ),
        )

    def on_fragment_recomp_finished(self, products: list, tool_title: str) -> None:
        self._finish_tool_progress(tool_title)
        method_label = "BRICS" if "BRICS" in tool_title.upper() else "RECAP"
        records = [
            (str(smi), {"Recompose_Method": method_label}) for smi in products if (smi or "").strip()
        ]
        n = self.add_rows_from_external_records_batch(records, render_structures=True)
        self.status_label.setText(
            self._consume_partial_results_notice()
            or f"{tool_title}: added {n:,} product row(s)."
        )

    def on_fragment_recomp_failed(self, message: str, tool_title: str) -> None:
        self._clear_tool_progress()
        self.status_label.setText("Ready.")
        QMessageBox.warning(self, tool_title, message or "Fragment recomposition failed.")

    def _run_calculator_from_dialog(self, dlg) -> None:
        from ..dialogs import CalculatorDialog

        if not isinstance(dlg, CalculatorDialog):
            return
        self.new_c = dlg.name_input.text().strip()
        if not self.new_c:
            QMessageBox.warning(self, TOOL_CALCULATOR, "Enter a name for the new column.")
            return
        expr = dlg.expr_input.text().strip()
        if not expr:
            QMessageBox.warning(self, TOOL_CALCULATOR, "Enter an expression to evaluate.")
            return
        only_selected = dlg.only_selected_rows()
        allowed = self._selected_oids_set() if only_selected else None
        if self._abort_if_only_selected_but_empty(only_selected, allowed, TOOL_CALCULATOR):
            return
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
        ps = self._tool_progress_state
        self._begin_tool_progress("Calculator…", len(row_data))
        self.process_queue.enqueue(
            f"Calculator ({len(row_data)} rows)",
            lambda ev, rd=row_data, ex=expr, sigs=self.signals, p=ps: CustomCalcWorker(
                rd, ex, sigs, cancel_event=ev, progress_state=p
            ),
        )

    def open_calculator(self):
        if not self.headers:
            return
        if load_config().disable_custom_calc:
            QMessageBox.information(
                self,
                TOOL_CALCULATOR,
                "The calculator is disabled by policy (environment variable MOLMANAGER_DISABLE_CUSTOM_CALC).",
            )
            return
        numeric_vars = list(self.global_bounds.keys())
        from ..dialogs import CalculatorDialog

        def _factory():
            d = CalculatorDialog(numeric_vars, len(self._selected_logical_rows()), self)
            d.setModal(False)
            d.setAttribute(Qt.WA_DeleteOnClose, True)
            d.apply_requested.connect(lambda dlg=d: self._run_calculator_from_dialog(dlg))
            self._prepare_tool_dialog(d)
            return d

        reuse_or_show_modeless_singleton(
            self,
            "_calculator_dialog",
            _factory,
            self._on_calculator_dialog_destroyed,
            on_reused_visible=lambda dlg: self._sync_dialog_only_selected_scope(dlg),
        )

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
        w = getattr(self, "_docked_plot_widget", None)
        if w is not None:
            try:
                self._plot_panel.setVisible(True)
                self._sync_dialog_only_selected_scope(w)
                self.activateWindow()
                self.raise_()
                return
            except RuntimeError:
                self._docked_plot_widget = None
        dlg = self._create_plot_dialog()
        self._register_plot_dialog(dlg)
        self._sync_dialog_only_selected_scope(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

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

    def open_patent_query(self):
        from ..external import PatentQueryDialog

        reuse_or_show_modeless_singleton(
            self,
            "_patent_query_dialog",
            lambda: PatentQueryDialog(self),
            self._on_patent_query_dialog_destroyed,
        )

    def open_boltz2(self):
        from ..external import Boltz2Dialog

        dlg = reuse_or_show_modeless_singleton(
            self,
            "_boltz2_dialog",
            lambda: Boltz2Dialog(self),
            self._on_boltz2_dialog_destroyed,
        )
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        self._prepare_tool_dialog(dlg)

    def open_vina_dock(self):
        from ..vina_dock import VinaDockDialog

        dlg = reuse_or_show_modeless_singleton(
            self,
            "_vina_dock_dialog",
            lambda: VinaDockDialog(self),
            self._on_vina_dock_dialog_destroyed,
        )
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        self._prepare_tool_dialog(dlg)

    def _ensure_columns(self, col_names: list[str]) -> None:
        """Ensure the table has these headers (adds columns to the right if needed)."""
        if not self.headers:
            self.headers = ["ID_HIDDEN", "Structure", "SMILES"]
            self._table_model.set_headers(list(self.headers))
            self.table.setColumnHidden(0, True)
        existing = {h: i for i, h in enumerate(self.headers)}
        to_add = [h for h in col_names if h not in existing]
        if to_add:
            col_at = len(self.headers)
            self.headers.extend(to_add)
            self._table_model.insert_columns_at(col_at, to_add, None)

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

        self._sync_global_bounds_for_headers(list(fields.keys()), refresh_filters=False)
        self.table.setSortingEnabled(False)

    def add_rows_from_external_records_batch(
        self,
        records: list[tuple[str, dict[str, str]]],
        *,
        render_structures: bool = True,
    ) -> int:
        """Append many external rows with one model notification (ChEMBL/PubChem/protomer adds)."""
        if not records:
            return 0
        field_names: set[str] = set()
        for _smi, fields in records:
            field_names.update(fields.keys())
        self._ensure_columns(["SMILES"] + sorted(field_names))
        self.table.setSortingEnabled(False)
        try:
            self.table.setUpdatesEnabled(False)
        except Exception:
            pass
        batch_rows: list[tuple[int, dict[str, str]]] = []
        new_mols: list[tuple[int, Chem.Mol]] = []
        for smiles, fields in records:
            smiles = (smiles or "").strip()
            if not smiles:
                continue
            oid = self.next_oid
            self.next_oid += 1
            row_cells: dict[str, str] = {}
            for h in self.headers[2:]:
                if h == "SMILES":
                    row_cells[h] = smiles
                else:
                    row_cells[h] = str(fields.get(h, "") or "")
            batch_rows.append((oid, row_cells))
            if render_structures:
                mol = Chem.MolFromSmiles(smiles)
                if mol is not None:
                    new_mols.append((oid, mol))
        if batch_rows:
            self._table_model.append_rows_batch(batch_rows)
            for oid, mol in new_mols:
                self.mols[oid] = mol
                self.start_render_worker(oid, mol)
            self._sync_global_bounds_for_headers(sorted(field_names), refresh_filters=False)
        try:
            self.table.setUpdatesEnabled(True)
        except Exception:
            pass
        self.table.setSortingEnabled(False)
        return len(batch_rows)

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
        """Load a SQL query/table into the main table.

        If a 'SMILES' column exists (case-insensitive), molecules will be created and
        2D structure images are drawn automatically (same as after opening a structure file).
        """
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
        page_size = max(128, int(sql_cfg.sqlite_backend_page_size))
        sql = ""
        cols: list[str] = []
        nrows = 0
        rows_hit_limit = False
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
            else:
                sql = query or ""
                if apply_limit and limit_eff:
                    # If the query already includes a LIMIT, leave it alone.
                    if re.search(r"\blimit\b", sql, flags=re.IGNORECASE) is None:
                        sql = f"SELECT * FROM ({sql}) AS subq LIMIT {int(limit_eff)}"
            perf = getattr(self, "_perf", None)
            scope = perf.track if perf is not None else (lambda *_args, **_kwargs: nullcontext())
            with scope("sql.load_rows"):
                try:
                    self.table.setUpdatesEnabled(False)
                except Exception:
                    pass
                rs = conn.execution_options(stream_results=True).execute(text(sql))
                cols = [str(c) for c in rs.keys()]
                if not cols:
                    rs.close()
                    raise RuntimeError("Query returned 0 rows.")

                if clear_first:
                    self.clear_all()

                # Build headers: keep the app's first two columns.
                self.headers = ["ID_HIDDEN", "Structure"] + cols
                self.table.setSortingEnabled(False)
                self._table_model.clear_rows()
                self._table_model.set_headers(list(self.headers))
                self.table.setColumnHidden(0, True)

                smiles_col = next((c for c in cols if c.lower() == "smiles"), None)

                # Reset molecule store.
                self.mols = {}
                self._clear_filter_target_smiles_cache()
                self.global_bounds = {}
                self.next_oid = 0

                while True:
                    chunk = rs.fetchmany(page_size)
                    if not chunk:
                        break
                    batch: list[tuple[int, dict[str, str]]] = []
                    for rec in chunk:
                        oid = self.next_oid
                        self.next_oid += 1
                        row_cells: dict[str, str] = {}
                        for c in cols:
                            v = rec._mapping.get(c)
                            row_cells[c] = "" if v is None else str(v)
                        batch.append((oid, row_cells))
                        if smiles_col is not None:
                            smi = (row_cells.get(smiles_col, "") or "").strip()
                            mol = Chem.MolFromSmiles(smi) if smi else None
                            if mol is not None:
                                self.mols[oid] = mol
                    if batch:
                        self._table_model.append_rows_batch(batch)
                        nrows += len(batch)
                        if apply_limit and limit_eff and nrows >= limit_eff:
                            rows_hit_limit = True
                rs.close()
                try:
                    self.table.setUpdatesEnabled(True)
                except Exception:
                    pass

        if nrows <= 0:
            raise RuntimeError("Query returned 0 rows.")

        if rows_hit_limit:
            QMessageBox.information(
                self,
                "SQL load",
                f"The result has {nrows:,} row(s), reaching the row limit ({limit_eff:,}). "
                "If you expected more rows, raise “Max rows” in the SQL dialog or adjust your query.",
            )

        if self._sqlite_store is not None:
            # Rebuild lazily on demand (filter/search) to keep ingest fast and memory flatter.
            self._sqlite_store_dirty = True

        self.calculate_global_bounds()
        self.table.setSortingEnabled(False)
        smiles_loaded = "SMILES" in self.headers
        if smiles_loaded and self._try_auto_render_all_structures_after_ingest():
            self.status_label.setText(f"Loaded {nrows} row(s) from SQL — drawing 2D structures…")
        else:
            self.status_label.setText(
                loaded_sql_status(nrows) if smiles_loaded else f"Loaded {nrows} row(s) from SQL (no SMILES column)."
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

    def _ensure_pka_predictor_signals(self):
        """Signals live on the main window so pKa jobs survive dialog close."""
        sig = getattr(self, "_pka_predictor_signals", None)
        if sig is not None:
            return sig
        from ...workers import PKaPredictorSignals

        sig = PKaPredictorSignals(self)
        sig.finished.connect(self._on_pka_prediction_finished)
        sig.failed.connect(self._on_pka_prediction_failed)
        self._pka_predictor_signals = sig
        return sig

    def _on_pka_prediction_finished(self, results: list) -> None:
        table_rows = [(o, t) for o, t in results if o is not None]
        lone = [t for o, t in results if o is None]
        if table_rows:
            res = [(int(o), {"pKa": text}) for o, text in table_rows]
            self.on_calc_finished(res, ["pKa"], progress_label="pKa prediction")
        if lone:
            QMessageBox.information(self, "pKa Predictor", lone[0])
        if not table_rows:
            self._finish_tool_progress("pKa prediction")
            self.status_label.setText("Ready.")

    def _on_pka_prediction_failed(self, msg: str) -> None:
        self._finish_tool_progress("pKa prediction")
        QMessageBox.warning(self, "pKa Predictor", msg or "Prediction failed.")

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

    def _ensure_permeability_predictor_signals(self):
        sig = getattr(self, "_permeability_predictor_signals", None)
        if sig is not None:
            return sig
        from ...workers import PermeabilityPredictorSignals

        sig = PermeabilityPredictorSignals(self)
        sig.finished.connect(self._on_permeability_prediction_finished)
        sig.failed.connect(self._on_permeability_prediction_failed)
        self._permeability_predictor_signals = sig
        return sig

    def _on_permeability_prediction_finished(self, results: list) -> None:
        if not results:
            self._finish_tool_progress("Predict Permeability")
            self.status_label.setText("Ready.")
            return
        calc_h = list(results[0][1].keys())
        res = [(oid, row_d) for oid, row_d in results]
        self.on_calc_finished(res, calc_h, progress_label="Predict Permeability")

    def _on_permeability_prediction_failed(self, msg: str) -> None:
        self._finish_tool_progress("Predict Permeability")
        QMessageBox.warning(self, "Predict Permeability", msg or "Prediction failed.")

    def open_permeability_predictor(self) -> None:
        if not self.headers:
            QMessageBox.information(
                self,
                "Predict Permeability",
                "Open a file or start a session first.",
            )
            return
        from ..dialogs import PermeabilityPredictorDialog

        dlg = PermeabilityPredictorDialog(self)
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

        self._finish_tool_progress("Calculator…")
        self.on_calc_finished(
            [(int(oid), {self.new_c: str(val)}) for oid, val in res],
            [self.new_c],
            finish_progress=False,
        )
        self.status_label.setText(
            self._consume_partial_results_notice()
            or f'{TOOL_CALCULATOR}: column "{self.new_c}" updated.'
        )

