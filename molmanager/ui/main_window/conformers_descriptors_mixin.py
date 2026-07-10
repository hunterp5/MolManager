"""Conformers, superposition, and descriptor calculation."""

from __future__ import annotations

import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QMessageBox,
)

from rdkit import Chem

from ...conformer_output import iter_single_conformer_mols, write_conformer_results_to_sdf
from ...confs_codec import (
    demote_v1_cell_to_sidecar,
    rehydrate_v1_confs_cell,
    unpack_confs_blocks_json_b64,
)
from ...utils import mol_to_canonical_smiles
from ..strings import (
    TOOL_SINGLE_CONFORMATION,
)
from ...descriptor_reuse import partition_descriptor_jobs
from ...workers import (
    CalcWorker,
    ConformerGenerationWorker,
    SuperposeConformersWorker,
)
from ..widgets import CategoryFilterCard, FilterCard, TextFilterCard

logger = logging.getLogger(__name__)

class ConformersDescriptorsMixin:
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
        self._conformer_output_options = d.output_options()
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
        self._conformer_output_options = d.output_options()
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
        """Request cooperative cancellation of the process-queue job, Render 2D, and/or Smina."""
        r2d = self.cancel_render_2d_batch()
        smina = self.cancel_smina_dock()
        pq_ok = self.process_queue.cancel_running()
        if pq_ok:
            self.status_label.setText("Cancelling…")
        elif r2d:
            self.status_label.setText("Render 2D cancelled.")
        elif smina:
            self.status_label.setText("Smina stopped.")
        else:
            QMessageBox.information(
                self,
                "Cancel Process",
                "Nothing to cancel (no process-queue job, Render 2D batch, or Smina run), "
                "or cancellation was already requested.",
            )

    def on_conformers_finished(self, results: list) -> None:
        self._finish_tool_progress("Generate conformations")
        output_opts = getattr(self, "_conformer_output_options", None)
        self._conformer_output_options = None
        added_rows = 0
        saved_count = 0
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
            if output_opts is not None and output_opts.add_to_table:
                added_rows = self._append_generated_conformers_as_rows(results)
            if output_opts is not None and output_opts.save_to_file and output_opts.save_path:
                try:
                    saved_count = write_conformer_results_to_sdf(output_opts.save_path, results)
                except OSError as e:
                    QMessageBox.warning(self, "Generate Conformations", f"Could not write SDF file:\n{e}")
            self.schedule_calculate_global_bounds()
            self.table.setSortingEnabled(False)
        finally:
            try:
                self.table.setUpdatesEnabled(True)
            except Exception:
                pass
        notice = self._consume_partial_results_notice()
        parts = []
        if notice:
            parts.append(notice)
        if output_opts is not None and output_opts.add_to_table:
            parts.append(f"Added {added_rows} conformer row(s) to the table.")
        if output_opts is not None and output_opts.save_to_file and output_opts.save_path:
            if saved_count:
                parts.append(f"Wrote {saved_count} conformer(s) to {output_opts.save_path}.")
            elif not any(p.startswith("Could not") for p in parts):
                parts.append("No conformers were written to the SDF file.")
        self.status_label.setText(" ".join(parts) if parts else "Done.")

    def _append_generated_conformers_as_rows(self, results: list) -> int:
        """Append one table row per generated conformer; keep 3D coordinates in ``self.mols``."""
        records: list[tuple[str, dict[str, str], Chem.Mol]] = []
        for item in results:
            if len(item) < 2:
                continue
            parent_oid, mol = int(item[0]), item[1]
            if mol is None:
                continue
            for conf_i, cm in enumerate(iter_single_conformer_mols(mol)):
                smi = mol_to_canonical_smiles(cm)
                if not smi:
                    continue
                records.append(
                    (
                        smi,
                        {
                            "Parent OID": str(parent_oid),
                            "Conformer": str(conf_i + 1),
                        },
                        cm,
                    )
                )
        if not records:
            return 0
        field_names: set[str] = set()
        for _smi, fields, _mol in records:
            field_names.update(fields.keys())
        self._ensure_columns(["SMILES"] + sorted(field_names))
        batch_rows: list[tuple[int, dict[str, str]]] = []
        new_mols: list[tuple[int, Chem.Mol]] = []
        for smiles, fields, mol in records:
            oid = self.next_oid
            self.next_oid += 1
            row_cells: dict[str, str] = {}
            for h in self.headers[2:]:
                if h == "SMILES":
                    row_cells[h] = smiles
                else:
                    row_cells[h] = str(fields.get(h, "") or "")
            batch_rows.append((oid, row_cells))
            new_mols.append((oid, mol))
        self._table_model.append_rows_batch(batch_rows)
        for oid, mol in new_mols:
            self.mols[oid] = mol
            self.start_render_worker(oid, mol)
        self._sync_global_bounds_for_headers(sorted(field_names), refresh_filters=False)
        return len(batch_rows)

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
            self.schedule_calculate_global_bounds()
            self.table.setSortingEnabled(False)
        finally:
            try:
                self.table.setUpdatesEnabled(True)
            except Exception:
                pass
        self.status_label.setText(self._consume_partial_results_notice() or "Done.")

    def _unique_table_column_names(self, bases: list[str]) -> list[str]:
        """Return column header names; append `` (n)`` when a name already exists in the table."""
        out: list[str] = []
        used = set(self.headers)
        for raw in bases:
            base = (raw or "").strip() or "Column"
            col = base
            if col in used:
                cnt = 1
                while f"{base} ({cnt})" in used:
                    cnt += 1
                col = f"{base} ({cnt})"
            out.append(col)
            used.add(col)
        return out

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
        calc_headers = self._unique_table_column_names(disp)
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

        compute_disp, compute_fns, calc_headers, skipped = partition_descriptor_jobs(
            disp,
            list(fns),
            calc_headers,
            oids_list,
            headers=list(self.headers),
            cell_text=self._table_cell_text,
            row_for_oid=self.get_row_by_id,
        )
        if skipped:
            preview = ", ".join(skipped[:4])
            if len(skipped) > 4:
                preview += f", … (+{len(skipped) - 4} more)"
            self.status_label.setText(
                f"Skipping {len(skipped)} already-calculated column(s): {preview}"
            )
        if not compute_disp:
            QMessageBox.information(
                self,
                "Calculate Descriptors",
                "All selected descriptors are already calculated for every row in this scope.",
            )
            self.status_label.setText("Ready.")
            return

        ps = self._tool_progress_state
        self._begin_tool_progress("Calculate descriptors", len(data))
        self.process_queue.enqueue(
            f"Calculate descriptors ({len(data)} rows)",
            lambda ev, d=data, dh=calc_headers, fn=compute_fns, sm=is_s, sigs=self.signals, p=ps: CalcWorker(
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
        refresh_search = getattr(self, "_refresh_table_search_column_combos", None)
        if callable(refresh_search):
            refresh_search()

    def _apply_calc_result_values(self, res, calc_h) -> list[str]:
        """Insert any new descriptor columns and write result values. Returns newly added headers."""
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
        return new_h

    def on_calc_partial(self, res, calc_h):
        """Apply a progressive chunk of descriptor results during a large computation.

        Columns fill incrementally so the table stays responsive; the terminal ``calculated``
        signal still performs the authoritative full apply and numeric-bounds refresh.
        """
        if not res:
            return
        try:
            self.table.setSortingEnabled(False)
        except Exception:
            pass
        self._apply_calc_result_values(res, calc_h)

    def on_calc_finished(self, res, calc_h, *, finish_progress: bool = True, progress_label: str | None = None):
        if finish_progress:
            self._finish_tool_progress(progress_label, status_message=None)
        self.table.setSortingEnabled(False)
        try:
            self.table.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            new_h = self._apply_calc_result_values(res, calc_h)
            if self._table_model.rowCount() >= 5000:
                dirty = {
                    h
                    for h in calc_h
                    if h in self._table_model._bounds_data_headers()
                }
                if dirty:
                    self._table_model._mark_numeric_bounds_dirty(dirty)
                self.schedule_calculate_global_bounds()
            else:
                self._sync_global_bounds_for_headers(calc_h, refresh_filters=bool(new_h))
            self.table.setSortingEnabled(False)
        finally:
            try:
                self.table.setUpdatesEnabled(True)
            except Exception:
                pass
        self.status_label.setText(self._consume_partial_results_notice() or "Done.")
