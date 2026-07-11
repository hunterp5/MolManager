"""Calculator, SQL load, external DB, and prediction dialogs."""

from __future__ import annotations

import logging
import re
import time
from contextlib import nullcontext

from PyQt5.QtCore import QEventLoop, Qt, QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox

from rdkit import Chem

from ...config import load_config
from ..compound_table_model import STRUCTURE_DEPICT_HEIGHT, STRUCTURE_DEPICT_WIDTH
from ...utils import redact_sqlalchemy_url, safe_float
from ..singleton_modeless_dialog import reuse_or_show_modeless_singleton
from ..strings import (
    TOOL_CALCULATOR,
    loaded_sql_status,
)
from ...workers import (
    CustomCalcWorker,
)

logger = logging.getLogger(__name__)

class ToolsSqlPredictMixin:
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

        def _factory() -> DataAnalysisDialog:
            dlg = DataAnalysisDialog(self)
            self._prepare_tool_dialog(dlg)
            return dlg

        def _on_reused(dlg: DataAnalysisDialog) -> None:
            self._sync_dialog_only_selected_scope(dlg)
            dlg._sync_selected_columns_only_scope()
            dlg.refresh_table_data()

        reuse_or_show_modeless_singleton(
            self,
            "_data_analysis_dialog",
            _factory,
            self._on_data_analysis_dialog_destroyed,
            on_reused_visible=_on_reused,
        )

    def open_plot(self):
        if not self.headers:
            return
        w = getattr(self, "_docked_plot_widget", None)
        if w is not None:
            try:
                self._plot_panel.setVisible(True)
                self._sync_dialog_only_selected_scope(w)
                self._sync_active_plots_from_table_selection()
                self.activateWindow()
                self.raise_()
                return
            except RuntimeError:
                self._docked_plot_widget = None
        dlg = self._create_plot_dialog()
        self._register_plot_dialog(dlg)
        self._sync_dialog_only_selected_scope(dlg)
        self._sync_active_plots_from_table_selection()
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

    def open_smina_dock(self):
        from ..smina_dock import SminaDockDialog

        dlg = reuse_or_show_modeless_singleton(
            self,
            "_smina_dock_dialog",
            lambda: SminaDockDialog(self),
            self._on_smina_dock_dialog_destroyed,
        )
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        self._prepare_tool_dialog(dlg)

    def open_dock_prepare(self):
        from ..dialogs.pdbqt_generator import PdbqtGeneratorDialog

        dlg = reuse_or_show_modeless_singleton(
            self,
            "_pdbqt_generator_dialog",
            lambda: PdbqtGeneratorDialog(self),
            self._on_pdbqt_generator_dialog_destroyed,
        )
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        self._prepare_tool_dialog(dlg)

    def open_dock_prepare_pdb(self):
        from ..dialogs.pdb_fixer import PdbFixerDialog

        dlg = reuse_or_show_modeless_singleton(
            self,
            "_pdb_fixer_dialog",
            lambda: PdbFixerDialog(self),
            self._on_pdb_fixer_dialog_destroyed,
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
        prepared = self._prepare_external_record_rows(records)
        if not prepared:
            return 0
        if len(prepared) == 1 or getattr(self, "_external_append_active", False):
            if len(prepared) > 1 and getattr(self, "_external_append_active", False):
                queue = getattr(self, "_external_append_queue", None)
                if queue is None:
                    self._external_append_queue = []
                    queue = self._external_append_queue
                queue.append((records, render_structures))
                return len(prepared)
            return self._add_external_records_batch_sync(
                prepared, sorted(field_names), render_structures=render_structures
            )
        self._external_append_active = True
        self._external_append_prepared = prepared
        self._external_append_index = 0
        self._external_append_field_names = sorted(field_names)
        self._external_append_render = render_structures
        self.table.setSortingEnabled(False)
        QTimer.singleShot(0, self._process_external_records_append_chunk)
        return len(prepared)

    def _prepare_external_record_rows(
        self, records: list[tuple[str, dict[str, str]]]
    ) -> list[tuple[int, dict[str, str]]]:
        prepared: list[tuple[int, dict[str, str]]] = []
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
            prepared.append((oid, row_cells))
        return prepared

    def _add_external_records_batch_sync(
        self,
        prepared: list[tuple[int, dict[str, str]]],
        field_names: list[str],
        *,
        render_structures: bool,
    ) -> int:
        """Append external rows immediately (small batches or when a deferred append is active)."""
        self.table.setSortingEnabled(False)
        try:
            self.table.setUpdatesEnabled(False)
        except Exception:
            pass
        new_mols: list[tuple[int, Chem.Mol]] = []
        if render_structures:
            for oid, row_cells in prepared:
                smi = (row_cells.get("SMILES", "") or "").strip()
                if not smi:
                    continue
                mol = Chem.MolFromSmiles(smi)
                if mol is not None:
                    new_mols.append((oid, mol))
        cfg = load_config()
        defer_color = len(prepared) >= int(cfg.bulk_update_defer_color_cache_rows)
        self._table_model.append_rows_batch(prepared, defer_color_cache=defer_color)
        for oid, mol in new_mols:
            self.mols[oid] = mol
            self.start_render_worker(oid, mol)
        if defer_color:
            self._table_model.rebuild_column_color_caches_after_bulk_load()
        self._sync_global_bounds_for_headers(field_names, refresh_filters=False)
        try:
            self.table.setUpdatesEnabled(True)
        except Exception:
            pass
        self.table.setSortingEnabled(False)
        return len(prepared)

    def _process_external_records_append_chunk(self) -> None:
        prepared = getattr(self, "_external_append_prepared", None)
        if not prepared:
            self._external_append_active = False
            return
        cfg = load_config()
        chunk_size = int(cfg.ingest_gui_chunk_size)
        budget_s = max(0.005, int(cfg.ingest_gui_time_budget_ms) / 1000.0)
        deadline = time.monotonic() + budget_s
        start = int(getattr(self, "_external_append_index", 0))
        try:
            self.table.setUpdatesEnabled(False)
        except Exception:
            pass
        batch_rows: list[tuple[int, dict[str, str]]] = []
        while start < len(prepared) and len(batch_rows) < chunk_size and time.monotonic() < deadline:
            batch_rows.append(prepared[start])
            start += 1
        self._external_append_index = start
        if batch_rows:
            self._table_model.append_rows_batch(batch_rows, defer_color_cache=True)
        try:
            self.table.setUpdatesEnabled(True)
        except Exception:
            pass
        if start < len(prepared):
            QTimer.singleShot(0, self._process_external_records_append_chunk)
        else:
            QTimer.singleShot(0, self._finalize_external_records_append)

    def _finalize_external_records_append(self) -> None:
        prepared = getattr(self, "_external_append_prepared", None) or []
        field_names = list(getattr(self, "_external_append_field_names", []) or [])
        render_structures = bool(getattr(self, "_external_append_render", False))
        oids = [oid for oid, _ in prepared]
        for attr in (
            "_external_append_prepared",
            "_external_append_index",
            "_external_append_field_names",
            "_external_append_render",
        ):
            try:
                delattr(self, attr)
            except AttributeError:
                pass
        self._external_append_active = False
        self.table.setSortingEnabled(False)
        self._table_model.rebuild_column_color_caches_after_bulk_load()
        QTimer.singleShot(
            0,
            lambda: self._sync_global_bounds_for_headers(field_names, refresh_filters=False),
        )
        if render_structures and oids:
            self._external_append_render_oids = list(oids)
            self._external_append_render_tasks = []
            self._external_append_render_row_by_oid = {}
            self._external_append_render_index = 0
            QTimer.singleShot(0, self._external_append_render_tasks_chunk)
        else:
            self._drain_external_append_queue()

    def _external_append_render_tasks_chunk(self) -> None:
        oids = getattr(self, "_external_append_render_oids", None)
        if not oids:
            self._drain_external_append_queue()
            return
        idx = int(getattr(self, "_external_append_render_index", 0))
        chunk = 64
        slice_oids = oids[idx : idx + chunk]
        base_w, base_h = STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT
        tasks, row_map = self._build_render2d_tasks_for_oids(slice_oids, base_w, base_h)
        self._external_append_render_tasks.extend(tasks)
        self._external_append_render_row_by_oid.update(row_map)
        idx += len(slice_oids)
        self._external_append_render_index = idx
        if idx < len(oids):
            QTimer.singleShot(0, self._external_append_render_tasks_chunk)
            return
        renders = list(getattr(self, "_external_append_render_tasks", []) or [])
        row_by_oid = dict(getattr(self, "_external_append_render_row_by_oid", {}) or {})
        for attr in (
            "_external_append_render_oids",
            "_external_append_render_tasks",
            "_external_append_render_row_by_oid",
            "_external_append_render_index",
        ):
            try:
                delattr(self, attr)
            except AttributeError:
                pass
        if renders:
            self._start_render_2d_batch(
                renders,
                row_by_oid,
                "Structure",
                column_pixmap_mode=False,
            )
        self._drain_external_append_queue()

    def _drain_external_append_queue(self) -> None:
        queue = getattr(self, "_external_append_queue", None)
        if not queue:
            return
        records, render_structures = queue.pop(0)
        if not queue:
            try:
                delattr(self, "_external_append_queue")
            except AttributeError:
                pass
        self.add_rows_from_external_records_batch(records, render_structures=render_structures)

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
                        self._table_model.append_rows_batch(batch, defer_color_cache=True)
                        nrows += len(batch)
                        if apply_limit and limit_eff and nrows >= limit_eff:
                            rows_hit_limit = True
                    app = QApplication.instance()
                    if app is not None:
                        app.processEvents(QEventLoop.ExcludeUserInputEvents)
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

        self.table.setSortingEnabled(False)
        smiles_loaded = "SMILES" in self.headers
        QTimer.singleShot(0, lambda: self._deferred_sql_post_load_follow_up(nrows, smiles_loaded))

    def _deferred_sql_post_load_follow_up(self, nrows: int, smiles_loaded: bool) -> None:
        """Defer bounds scan and 2D batch so the SQL load dialog can close and the table can paint."""
        self._table_model.rebuild_column_color_caches_after_bulk_load()
        self.schedule_calculate_global_bounds(delay_ms=500)
        if smiles_loaded and self._try_auto_render_all_structures_after_ingest():
            self.status_label.setText(f"Loaded {nrows:,} row(s) from SQL — drawing 2D structures…")
        else:
            self.status_label.setText(
                loaded_sql_status(nrows) if smiles_loaded else f"Loaded {nrows:,} row(s) from SQL (no SMILES column)."
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

    def open_diverse_subset(self) -> None:
        if not self.headers:
            return
        from ..dialogs import DiverseSubsetDialog

        dlg = DiverseSubsetDialog(self)
        self._prepare_tool_dialog(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _ensure_diverse_subset_signals(self):
        """Signals on the main window so diverse subset jobs survive dialog close."""
        sig = getattr(self, "_diverse_subset_signals", None)
        if sig is not None:
            return sig
        from ...workers import DiverseSubsetSignals

        sig = DiverseSubsetSignals(self)
        sig.finished.connect(self._on_diverse_subset_finished)
        sig.failed.connect(self._on_diverse_subset_failed)
        self._diverse_subset_signals = sig
        return sig

    def _on_diverse_subset_finished(
        self, picked_oids: list, column_rows: list, n_cached: int, n_computed: int
    ) -> None:
        from PyQt5.QtCore import QTimer

        self._finish_tool_progress("Diverse subset")
        ctx = getattr(self, "_diverse_subset_run_ctx", None) or {}
        picked = [int(o) for o in (picked_oids or [])]

        def _apply_results() -> None:
            if ctx.get("select_subset") and picked:
                self.select_table_oids(picked, extra_status="")
            col_name = (ctx.get("pending_column_name") or "").strip()
            if col_name and column_rows:
                scope_oids = ctx.get("scope_oids") or set()
                oid_map = {int(oid): rank for oid, rank in column_rows}
                full_map = {oid: oid_map.get(oid, "") for oid in scope_oids}
                m = self._table_model
                nc = m.columnCount()
                self.headers.append(col_name)
                m.insert_column_at(nc, col_name, None)
                try:
                    self.table.setUpdatesEnabled(False)
                except Exception:
                    pass
                try:
                    m.fill_column_from_oid_map(col_name, full_map, default="")
                    self._sync_global_bounds_for_headers([col_name], refresh_filters=True)
                finally:
                    try:
                        self.table.setUpdatesEnabled(True)
                    except Exception:
                        pass
            cache_note = ""
            if n_cached or n_computed:
                cache_note = f" ({n_cached} cached fingerprint(s), {n_computed} computed)"
            col_note = f" Column '{col_name}' added." if col_name else ""
            self.status_label.setText(
                f"Diverse subset: picked {len(picked)} compound(s).{cache_note}{col_note}"
            )

        QTimer.singleShot(0, _apply_results)

    def _on_diverse_subset_failed(self, msg: str) -> None:
        self._finish_tool_progress("Diverse subset")
        if msg == "Cancelled.":
            self.status_label.setText("Cancelled.")
        else:
            self.status_label.setText(
                f"Diverse subset failed: {msg or 'Computation failed.'}"
            )

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
        sig.finished.connect(self._on_permeability_prediction_finished, Qt.QueuedConnection)
        sig.failed.connect(self._on_permeability_prediction_failed, Qt.QueuedConnection)
        self._permeability_predictor_signals = sig
        return sig

    def schedule_permeability_prediction(
        self,
        src: str,
        *,
        only_selected: bool,
        output_columns: tuple[str, ...],
    ) -> None:
        """Gather rows and enqueue prediction on the next event-loop tick (keeps the dialog responsive)."""
        QTimer.singleShot(
            0,
            lambda: self._start_permeability_prediction(src, only_selected, output_columns),
        )

    def _start_permeability_prediction(
        self,
        src: str,
        only_selected: bool,
        output_columns: tuple[str, ...],
    ) -> None:
        from ...workers import PermeabilityPredictorWorker

        allowed = self._selected_oids_set() if only_selected else None
        if only_selected and not allowed:
            QMessageBox.warning(
                self,
                "Predict Permeability",
                "\u201cOnly selected rows\u201d is checked but nothing is selected.",
            )
            return
        rows_smi = self.collect_scoped_table_smiles(src, only_selected=only_selected)
        if not rows_smi:
            QMessageBox.information(
                self,
                "Predict Permeability",
                "No valid structures were found for this scope and source.",
            )
            return
        perm_signals = self._ensure_permeability_predictor_signals()
        n = len(rows_smi)
        prog = self._tool_progress_state
        self._begin_tool_progress("Predict Permeability", n)
        self.process_queue.enqueue(
            f"Predict Permeability ({n} rows)",
            lambda ev, r=rows_smi, ws=self.signals, ps=perm_signals, c=output_columns, st=prog: PermeabilityPredictorWorker(
                r, ws, ps, cancel_event=ev, output_columns=c, progress_state=st
            ),
        )

    def _on_permeability_prediction_finished(self, results: list) -> None:
        if not results:
            self._finish_tool_progress("Predict Permeability")
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

