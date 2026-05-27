"""File ingest, SQLite rebuild, and 2D structure rendering."""

from __future__ import annotations

import logging
import time
from contextlib import nullcontext

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QMessageBox,
)


from ...config import load_config
from ...display_constants import STRUCTURE_ROW_DEFAULT_HEIGHT
from ...structure_render_store import StructureRenderStore
from ..strings import (
    LOADING_DETAIL_AFTER_FILE_READ,
    STATUS_READY_RENDER_2D,
    loaded_session_status,
)
from ...storage import SqliteTableStore
from ...workers import (
    SqliteRebuildWorker,
)
from ..compound_table_model import (
    CompoundTableModel,
    STRUCTURE_COLUMN_HORIZONTAL_PADDING,
    STRUCTURE_DEPICT_HEIGHT,
    STRUCTURE_DEPICT_WIDTH,
)

logger = logging.getLogger(__name__)

class IngestRenderMixin:
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
                self.schedule_calculate_global_bounds()
                if getattr(self, "_sqlite_store", None) is not None:
                    self._sqlite_store_dirty = True
                else:
                    self._rebuild_sqlite_store_from_model()
                self.table.setSortingEnabled(False)
                self._ingest_loading = False
                self._structures_queued = 0
                self._table_stack.setCurrentIndex(1)
                self._import_progress_active = False
                self._clear_tool_progress(status_message=None)
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
        """Export in UI chunks, then rebuild the SQLite mirror in a background worker."""
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
        import os
        import tempfile
        from pathlib import Path

        fd, db_path = tempfile.mkstemp(prefix="MOLMANAGER_sqlite_", suffix=".sqlite3")
        try:
            os.close(fd)
        except OSError:
            pass
        self._sqlite_rebuild_pending_path = Path(db_path)
        n_rows = self._table_model.rowCount()
        from ..background_jobs import register_background_job

        job_id = f"sqlite-rebuild-{gen}"
        self._sqlite_rebuild_bg_job_id = job_id
        register_background_job(self, job_id, f"Indexing table ({n_rows:,} rows)")
        chunk = max(500, load_config().ingest_gui_chunk_size)
        self._sqlite_export_ctx = {
            "gen": gen,
            "data_headers": data_headers,
            "entries": [],
            "row_idx": 0,
            "n_rows": n_rows,
            "chunk": chunk,
            "db_path": str(db_path),
            "signals": sigs,
        }
        self.status_label.setText(f"Indexing table… (0/{n_rows:,} rows)")
        QTimer.singleShot(0, self._sqlite_export_chunk_step)

    def _sqlite_export_chunk_step(self) -> None:
        ctx = getattr(self, "_sqlite_export_ctx", None)
        if not ctx or ctx.get("gen") != getattr(self, "_sqlite_rebuild_gen", -1):
            return
        if not self._sqlite_rebuild_in_progress:
            self._sqlite_export_ctx = None
            return
        data_headers = ctx["data_headers"]
        row_idx = int(ctx["row_idx"])
        n_rows = int(ctx["n_rows"])
        chunk = int(ctx["chunk"])
        end = min(row_idx + chunk, n_rows)
        ctx["entries"].extend(self._table_model.export_rows_for_sqlite_slice(data_headers, row_idx, end))
        ctx["row_idx"] = end
        self.status_label.setText(f"Indexing table… ({end:,}/{n_rows:,} rows)")
        if end < n_rows:
            QTimer.singleShot(0, self._sqlite_export_chunk_step)
            return
        gen = int(ctx["gen"])
        entries = ctx["entries"]
        db_path = ctx["db_path"]
        sigs = ctx["signals"]
        self._sqlite_export_ctx = None
        pool = getattr(self, "threadpool", None)
        if pool is None:
            self._sqlite_rebuild_in_progress = False
            return
        pool.start(SqliteRebuildWorker(gen, list(self.headers), entries, db_path, sigs))

    def _unregister_sqlite_rebuild_background_job(self, job_gen: int) -> None:
        from ..background_jobs import unregister_background_job

        job_id = getattr(self, "_sqlite_rebuild_bg_job_id", None)
        if job_id == f"sqlite-rebuild-{job_gen}":
            unregister_background_job(self, job_id)
            self._sqlite_rebuild_bg_job_id = None

    def _on_sqlite_rebuild_finished(self, job_gen: int, db_path: str) -> None:
        self._unregister_sqlite_rebuild_background_job(job_gen)
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
        self._unregister_sqlite_rebuild_background_job(job_gen)
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
