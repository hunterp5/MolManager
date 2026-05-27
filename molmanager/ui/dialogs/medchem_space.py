"""BOILED-Egg and golden-triangle plots (Data menu)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...config import load_config
from ...plot_color import (
    PLOT_COLORSCALE_CHOICES,
    color_values_are_numeric,
    resolve_plot_colorscale,
)
from ..plot_color_range_controls import PlotColorRangeControls
from ...utils import mol_to_canonical_smiles
from ...medchem_space import (
    MedChemRowSnapshot,
    MedChemSpaceBuildResult,
    MedChemSpaceDataset,
    medchem_plot_max_points,
    oids_in_egg_gia,
    oids_in_egg_yolk,
    oids_in_golden_triangle_region,
    required_descriptor_columns_ok,
    resolve_descriptor_column,
    snapshot_scope_row_indices,
)
from ...workers.medchem_space_worker import MedChemSpaceSignals, MedChemSpaceWorker
from ..data_analysis import numeric_subset, table_to_dataframe
from ..medchem_space_plot import build_boiled_egg_figure, build_golden_triangle_figure
from ..plotly_interactive_view import PlotlyInteractiveView
from ..qt_widget_utils import apply_monospace_to_text_edit, make_window_minimizable
from .scope import selection_scope_checked

if TYPE_CHECKING:
    from ..main_window import ChemicalTableApp

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: F401

    _HAS_WEB = True
except ImportError:
    _HAS_WEB = False


MedChemPlotKind = Literal["boiled_egg", "golden_triangle"]


class MedChemPlotPanel(QWidget):
    """BOILED-Egg or golden-triangle controls + plot (dialog or docked beside the table)."""

    def __init__(
        self,
        parent_app: ChemicalTableApp | None,
        *,
        plot_kind: MedChemPlotKind,
        window_title: str,
    ):
        super().__init__(None)
        self.parent_app = parent_app
        self._plot_kind = plot_kind
        self._window_title = window_title
        self._full_dataset: MedChemSpaceDataset | None = None
        self._plot_dataset: MedChemSpaceDataset | None = None
        self._job_running = False
        self._snapshot_collect_gen = 0
        self._snapshot_collect_ctx: dict | None = None
        self._medchem_signals = MedChemSpaceSignals(self)
        self._medchem_signals.finished.connect(self._on_build_finished, Qt.QueuedConnection)
        self._medchem_signals.failed.connect(self._on_build_failed, Qt.QueuedConnection)
        self._table_write_ctx: dict | None = None
        self._table_write_gen = 0

        n_sel = len(parent_app._selected_logical_rows()) if parent_app is not None else 0
        self._have_selection = n_sel > 0

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        plot_host = QWidget()
        plot_ly = QVBoxLayout(plot_host)
        plot_ly.setContentsMargins(0, 0, 0, 0)
        if _HAS_WEB and parent_app is not None:
            self._plot_view = PlotlyInteractiveView(parent_app, plot_host)
            self._plot_view.setMinimumHeight(280)
            plot_ly.addWidget(self._plot_view, 1)
            self._plot_placeholder = None
        else:
            self._plot_view = None
            self._plot_placeholder = QLabel(
                "Install PyQtWebEngine to show the interactive plot in this window."
            )
            self._plot_placeholder.setWordWrap(True)
            self._plot_placeholder.setAlignment(Qt.AlignCenter)
            plot_ly.addWidget(self._plot_placeholder, 1)
        root.addWidget(plot_host, 1)

        opts = QVBoxLayout()
        opts.setSpacing(6)

        scope = QHBoxLayout()
        if plot_kind == "golden_triangle":
            self.select_region_btn = QPushButton("Select in triangle")
            self.select_region_btn.clicked.connect(self._on_select_in_triangle)
            scope.addWidget(self.select_region_btn)
        else:
            self.select_egg_btn = QPushButton("Select in egg")
            self.select_egg_btn.clicked.connect(self._on_select_in_egg)
            self.select_yolk_btn = QPushButton("Select in yolk")
            self.select_yolk_btn.clicked.connect(self._on_select_in_yolk)
            scope.addWidget(self.select_egg_btn)
            scope.addWidget(self.select_yolk_btn)
        self.refresh_btn = QPushButton("Refresh plot")
        self.refresh_btn.clicked.connect(self._on_refresh)
        scope.addWidget(self.refresh_btn)
        scope.addStretch(1)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({n_sel} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        self.only_selected_cb.stateChanged.connect(self._on_scope_changed)
        scope.addWidget(self.only_selected_cb)
        opts.addLayout(scope)

        struct_row = QHBoxLayout()
        struct_row.addWidget(QLabel("Structure from:"))
        self.struct_src_combo = QComboBox()
        struct_row.addWidget(self.struct_src_combo, 1)
        opts.addLayout(struct_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        self._color_by_label = QLabel("Color by:")
        color_row.addWidget(self._color_by_label)
        self.color_combo = QComboBox()
        self.color_combo.setMinimumWidth(120)
        self.color_combo.currentIndexChanged.connect(self._on_color_column_changed)
        color_row.addWidget(self.color_combo)
        self._spectrum_label = QLabel("Spectrum:")
        color_row.addWidget(self._spectrum_label)
        self.colorscale_combo = QComboBox()
        self.colorscale_combo.setMinimumWidth(100)
        self.colorscale_combo.addItems(PLOT_COLORSCALE_CHOICES)
        self.colorscale_combo.setToolTip("Continuous colorscale for numeric Color by columns.")
        self.colorscale_combo.currentIndexChanged.connect(self._on_color_column_changed)
        color_row.addWidget(self.colorscale_combo)
        self.color_range = PlotColorRangeControls()
        self.color_range.connect_changed(self._on_color_column_changed)
        color_row.addWidget(self.color_range)
        color_row.addStretch()
        opts.addLayout(color_row)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumHeight(52)
        apply_monospace_to_text_edit(self.summary_text)
        opts.addWidget(QLabel("Summary"))
        opts.addWidget(self.summary_text)

        root.addLayout(opts)

        self._refresh_structure_sources()
        self._reload_color_columns()
        self._update_spectrum_controls()
        self.summary_text.setPlainText("Preparing plot…")
        QTimer.singleShot(0, self._start_refresh_job)

    def create_floating_dialog(self, parent_app: ChemicalTableApp) -> MedChemSpaceDialog:
        """Re-open this panel in a floating window after undocking from the main table."""
        return MedChemSpaceDialog(
            parent_app,
            plot_kind=self._plot_kind,
            window_title=self._window_title,
            panel=self,
        )

    def _refresh_structure_sources(self) -> None:
        self.struct_src_combo.clear()
        if self.parent_app is None:
            return
        self.struct_src_combo.addItems(self.parent_app.chemistry_tool_structure_sources())

    def _reload_color_columns(self) -> None:
        prev = self.color_combo.currentText()
        self.color_combo.blockSignals(True)
        try:
            self.color_combo.clear()
            self.color_combo.addItem("(none)")
            if self.parent_app is None:
                return
            model = self.parent_app._table_model
            if model.rowCount() > 8000:
                for col in model._sorted_bounds_data_headers():
                    self.color_combo.addItem(col)
            else:
                only_sel = selection_scope_checked(self)
                df, _rows = table_to_dataframe(
                    self.parent_app, visible_only=True, only_selected=only_sel
                )
                for col in numeric_subset(df, exclude_id=True).columns:
                    self.color_combo.addItem(col)
            idx = self.color_combo.findText(prev)
            if idx >= 0:
                self.color_combo.setCurrentIndex(idx)
        finally:
            self.color_combo.blockSignals(False)

    def _on_scope_changed(self) -> None:
        self._reload_color_columns()

    def _update_spectrum_controls(self) -> None:
        enabled = self.color_combo.currentText() != "(none)"
        self._spectrum_label.setEnabled(enabled)
        self.colorscale_combo.setEnabled(enabled)
        numeric = False
        if enabled and self._plot_dataset is not None:
            color_col = self.color_combo.currentText()
            vals = self._color_values_for_oids(self._plot_dataset.oids, color_col)
            numeric = color_values_are_numeric(vals)
        self.color_range.set_enabled(enabled and numeric)

    def _current_color_bounds(self) -> tuple[float | None, float | None]:
        return self.color_range.parse_bounds()

    def _current_colorscale(self) -> str:
        return resolve_plot_colorscale(self.colorscale_combo.currentText())

    def _on_color_column_changed(self, _index: int = 0) -> None:
        self._update_spectrum_controls()
        self._push_plot_figure()

    def _descriptor_column_names(self) -> tuple[str | None, str | None, str | None, str | None]:
        assert self.parent_app is not None
        headers = list(self.parent_app.headers)
        logp_col = resolve_descriptor_column(headers, ("logp", "clogp", "mollogp", "wlogp"))
        tpsa_col = resolve_descriptor_column(headers, ("tpsa", "psa"))
        mw_col = resolve_descriptor_column(headers, ("mw", "molwt", "molecular weight"))
        wlogp_col = (
            logp_col
            if self._plot_kind == "boiled_egg"
            else resolve_descriptor_column(headers, ("wlogp",))
        )
        return tpsa_col, logp_col, mw_col, wlogp_col

    def _set_refresh_ui_busy(self, busy: bool) -> None:
        self._job_running = busy
        for w in (
            self.refresh_btn,
            self.struct_src_combo,
            self.only_selected_cb,
            getattr(self, "select_region_btn", None),
            getattr(self, "select_egg_btn", None),
            getattr(self, "select_yolk_btn", None),
        ):
            if w is not None:
                w.setEnabled(not busy)

    def _structure_text_for_row(self, row: int, src: str) -> str:
        """Read stored cell text only (no RDKit parsing on the UI thread)."""
        assert self.parent_app is not None
        model = self.parent_app._table_model
        if src == "Structure":
            return (model.backing_value_for_row_header(row, "Structure") or "").strip()
        raw = (model.value_for_header(row, src) or "").strip()
        if raw:
            return raw
        if model.is_pixmap_data_column(src):
            return (model.backing_value_for_row_header(row, src) or "").strip()
        return raw

    def _oid_smiles_from_cache(self, snapshots: list[MedChemRowSnapshot]) -> dict[int, str]:
        """SMILES from in-memory mols for rows without structure cell text."""
        app = self.parent_app
        if app is None:
            return {}
        out: dict[int, str] = {}
        for snap in snapshots:
            if (snap.structure_text or "").strip():
                continue
            mol = app.mols.get(int(snap.oid))
            if mol is None:
                continue
            smi = mol_to_canonical_smiles(mol)
            if smi:
                out[int(snap.oid)] = smi
        return out

    def _snapshot_collect_chunk_size(self) -> int:
        cfg = load_config()
        return max(4000, int(cfg.table_selection_chunk_rows) * 2)

    def _cancel_snapshot_collect(self) -> None:
        self._snapshot_collect_gen += 1
        self._snapshot_collect_ctx = None

    def _snapshot_scope_rows(self) -> list[int]:
        app = self.parent_app
        assert app is not None
        only_sel = selection_scope_checked(self)
        if only_sel and not app._selected_oids_set():
            raise ValueError("\u201cOnly selected rows\u201d is checked but nothing is selected.")
        only_rows = app._selected_logical_rows() if only_sel else None
        visible_rows = (
            app._visible_source_row_indices()
        )
        return snapshot_scope_row_indices(
            app._table_model.rowCount(),
            only_selected_rows=only_rows,
            visible_row_indices=visible_rows,
        )

    def _snapshot_from_row(
        self,
        row: int,
        *,
        model,
        src: str,
        id_col: str | None,
        tpsa_col: str | None,
        logp_col: str | None,
        mw_col: str | None,
        wlogp_col: str | None,
    ) -> MedChemRowSnapshot:
        oid = int(model.row_oid(row))

        def cell(header: str | None) -> str:
            return model.value_for_header(row, header) if header else ""

        label = cell(id_col) if id_col else f"OID {oid}"
        return MedChemRowSnapshot(
            oid=oid,
            label=label,
            structure_text=self._structure_text_for_row(row, src),
            tpsa_text=cell(tpsa_col),
            wlogp_text=cell(wlogp_col),
            mw_text=cell(mw_col),
            logp_text=cell(logp_col),
        )

    def _begin_snapshot_collect(self) -> None:
        if self.parent_app is None:
            return
        try:
            scope_rows = self._snapshot_scope_rows()
        except ValueError as exc:
            QMessageBox.warning(self, self._window_title, str(exc))
            return
        if not scope_rows:
            QMessageBox.information(
                self,
                self._window_title,
                "No rows in the current scope.",
            )
            return
        tpsa_col, logp_col, mw_col, wlogp_col = self._descriptor_column_names()
        app = self.parent_app
        id_col = resolve_descriptor_column(
            app.headers, ("id", "name", "compound", "compound name")
        )
        self._cancel_snapshot_collect()
        gen = self._snapshot_collect_gen
        self._snapshot_collect_ctx = {
            "gen": gen,
            "scope_rows": scope_rows,
            "idx": 0,
            "snapshots": [],
            "chunk": self._snapshot_collect_chunk_size(),
            "src": self.struct_src_combo.currentText(),
            "id_col": id_col,
            "tpsa_col": tpsa_col,
            "logp_col": logp_col,
            "mw_col": mw_col,
            "wlogp_col": wlogp_col,
        }
        self._set_refresh_ui_busy(True)
        total = len(scope_rows)
        self.summary_text.setPlainText(f"Reading table… (0/{total:,})")
        QTimer.singleShot(0, self._snapshot_collect_step)

    def _snapshot_collect_step(self) -> None:
        ctx = self._snapshot_collect_ctx
        if not ctx or ctx.get("gen") != self._snapshot_collect_gen:
            return
        app = self.parent_app
        if app is None:
            self._cancel_snapshot_collect()
            self._set_refresh_ui_busy(False)
            return
        scope_rows: list[int] = ctx["scope_rows"]
        idx = int(ctx["idx"])
        chunk = int(ctx["chunk"])
        end = min(idx + chunk, len(scope_rows))
        model = app._table_model
        snapshots: list[MedChemRowSnapshot] = ctx["snapshots"]
        for r in scope_rows[idx:end]:
            snapshots.append(
                self._snapshot_from_row(
                    int(r),
                    model=model,
                    src=str(ctx["src"]),
                    id_col=ctx.get("id_col"),
                    tpsa_col=ctx.get("tpsa_col"),
                    logp_col=ctx.get("logp_col"),
                    mw_col=ctx.get("mw_col"),
                    wlogp_col=ctx.get("wlogp_col"),
                )
            )
        ctx["idx"] = end
        self.summary_text.setPlainText(f"Reading table… ({end:,}/{len(scope_rows):,})")
        if end < len(scope_rows):
            QTimer.singleShot(0, self._snapshot_collect_step)
            return
        self._snapshot_collect_ctx = None
        self._launch_medchem_worker(snapshots)

    def _launch_medchem_worker(self, snapshots: list[MedChemRowSnapshot]) -> None:
        assert self.parent_app is not None
        tpsa_col, logp_col, mw_col, wlogp_col = self._descriptor_column_names()
        use_table = required_descriptor_columns_ok(
            self._plot_kind,
            tpsa_col=tpsa_col,
            logp_col=logp_col,
            mw_col=mw_col,
            wlogp_col=wlogp_col,
        )
        if not use_table and len(snapshots) > 5000:
            self.parent_app.status_label.setText(
                f"{self._window_title}: computing descriptors in the background for "
                f"{len(snapshots):,} compound(s)…"
            )
        if use_table:
            self.summary_text.setPlainText(
                f"Loading descriptors from table for {len(snapshots):,} compound(s)…"
            )
            oid_smiles: dict[int, str] = {}
        else:
            self.summary_text.setPlainText(
                f"Computing descriptors for {len(snapshots):,} compound(s)…"
            )
            oid_smiles = self._oid_smiles_from_cache(snapshots)
        self.parent_app._begin_tool_progress(self._window_title, len(snapshots))
        params = {
            "snapshots": snapshots,
            "plot_kind": self._plot_kind,
            "tpsa_col": tpsa_col,
            "logp_col": logp_col,
            "mw_col": mw_col,
            "wlogp_col": wlogp_col,
            "use_table_columns_only": use_table,
            "max_plot_points": medchem_plot_max_points(),
            "oid_smiles": oid_smiles,
            "progress_state": self.parent_app._tool_progress_state,
            "progress_label": self._window_title,
        }
        from ..background_jobs import register_background_job

        self._bg_job_id = f"medchem-{id(self)}"
        register_background_job(self.parent_app, self._bg_job_id, self._window_title)
        worker = MedChemSpaceWorker(params, self._medchem_signals)
        self.parent_app.threadpool.start(worker)

    def _clear_medchem_background_job(self) -> None:
        job_id = getattr(self, "_bg_job_id", None)
        if job_id and self.parent_app is not None:
            from ..background_jobs import unregister_background_job

            unregister_background_job(self.parent_app, job_id)
        self._bg_job_id = None

    def _start_refresh_job(self) -> None:
        self._on_refresh()

    def _on_refresh(self) -> None:
        if self.parent_app is None or self._job_running:
            return
        self._cancel_snapshot_collect()
        self._begin_snapshot_collect()

    def _on_build_finished(self, result: object) -> None:
        self._clear_medchem_background_job()
        self._set_refresh_ui_busy(False)
        if self.parent_app is not None:
            self.parent_app._finish_tool_progress(self._window_title)
        if not isinstance(result, MedChemSpaceBuildResult):
            return
        self._full_dataset = result.full
        self._plot_dataset = result.plot
        total = len(self._full_dataset.points)
        self.summary_text.setPlainText(
            self._full_dataset.summary_text(plot_kind=self._plot_kind, total_in_scope=total)
        )
        if result.table_updates:
            self._start_table_write_job(list(result.table_updates), list(result.table_columns))
        if self._plot_view is None:
            return
        if not self._plot_dataset.points:
            skipped = self._full_dataset.skipped if self._full_dataset else 0
            detail = (
                f"Could not compute descriptors for any compound in this scope "
                f"({skipped:,} row(s) skipped)."
            )
            if skipped:
                detail += (
                    "\n\nEnsure TPSA, LogP, and MolWt columns are populated, or choose a "
                    "structure source with valid SMILES/mol blocks (Structure column)."
                )
            QMessageBox.information(self, self._window_title, detail)
            return
        self._push_plot_figure()
        if self.parent_app is not None:
            shown = len(self._plot_dataset.points)
            n_written = len(result.table_updates)
            extra = f"; wrote descriptors for {n_written:,} row(s)" if n_written else ""
            self.parent_app.status_label.setText(
                f"{self._window_title}: showing {shown:,} of {total:,} compound(s){extra}."
            )

    def _on_build_failed(self, message: str) -> None:
        self._clear_medchem_background_job()
        self._set_refresh_ui_busy(False)
        if self.parent_app is not None:
            self.parent_app._finish_tool_progress()
        self.summary_text.setPlainText("")
        QMessageBox.warning(self, self._window_title, message or "Plot build failed.")

    def _cancel_table_write_job(self) -> None:
        self._table_write_gen += 1
        self._table_write_ctx = None

    def _start_table_write_job(
        self,
        updates: list[tuple[int, dict[str, str]]],
        columns: list[str],
    ) -> None:
        app = self.parent_app
        if app is None or not updates or not columns:
            return
        app._ensure_columns(columns)
        self._cancel_table_write_job()
        gen = self._table_write_gen
        chunk = max(500, load_config().table_selection_chunk_rows // 4)
        self._table_write_ctx = {
            "gen": gen,
            "updates": updates,
            "columns": columns,
            "idx": 0,
            "chunk": chunk,
        }
        app._begin_tool_progress(f"{self._window_title}: writing descriptors", len(updates))
        QTimer.singleShot(0, self._table_write_step)

    def _table_write_step(self) -> None:
        ctx = self._table_write_ctx
        app = self.parent_app
        if not ctx or ctx.get("gen") != self._table_write_gen or app is None:
            return
        updates: list[tuple[int, dict[str, str]]] = ctx["updates"]
        columns: list[str] = ctx["columns"]
        idx = int(ctx["idx"])
        chunk = int(ctx["chunk"])
        end = min(idx + chunk, len(updates))
        batch = updates[idx:end]
        if batch:
            try:
                app.table.setUpdatesEnabled(False)
            except Exception:
                pass
            try:
                app._table_model.apply_columns_values_bulk(columns, batch)
            finally:
                try:
                    app.table.setUpdatesEnabled(True)
                except Exception:
                    pass
        ctx["idx"] = end
        app._tool_progress_state.update(
            f"{self._window_title}: writing descriptors", end, len(updates)
        )
        if end < len(updates):
            QTimer.singleShot(0, self._table_write_step)
            return
        self._table_write_ctx = None
        app._sync_global_bounds_for_headers(columns, refresh_filters=True)
        app._finish_tool_progress(f"{self._window_title}: descriptors written")

    @staticmethod
    def _parse_numeric_cell(text: str) -> float | None:
        s = (text or "").strip().replace(",", "")
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
        return v if v == v else None

    def _color_values_for_oids(self, oids: list[int], color_col: str | None) -> list[Any] | None:
        """One color value per plotted OID, in ``dataset.oids`` order (raw table cell)."""
        if not color_col or color_col == "(none)" or self.parent_app is None:
            return None
        model = self.parent_app._table_model
        out: list[Any] = []
        for oid in oids:
            row = self.parent_app.get_row_by_id(int(oid))
            if row < 0:
                out.append(None)
                continue
            raw = model.value_for_header(row, color_col)
            out.append(raw if (raw or "").strip() else None)
        return out

    def _push_plot_figure(self) -> None:
        if self._plot_view is None or self._plot_dataset is None or not self._plot_dataset.points:
            return
        color_col = self.color_combo.currentText()
        if color_col == "(none)":
            color_col = None
        color_vals = self._color_values_for_oids(self._plot_dataset.oids, color_col)
        from ...plot_color import normalize_color_column

        color_vals, color_col = normalize_color_column(color_vals, color_col)
        colorscale = self._current_colorscale()
        color_min, color_max = self._current_color_bounds()
        try:
            if self._plot_kind == "golden_triangle":
                fig = build_golden_triangle_figure(
                    self._plot_dataset,
                    color_values=color_vals,
                    color_label=color_col,
                    colorscale=colorscale,
                    color_min=color_min,
                    color_max=color_max,
                )
            else:
                fig = build_boiled_egg_figure(
                    self._plot_dataset,
                    color_values=color_vals,
                    color_label=color_col,
                    colorscale=colorscale,
                    color_min=color_min,
                    color_max=color_max,
                )
            self._plot_view.push_figure(fig, self._plot_dataset.oids)
            self._update_spectrum_controls()
        except Exception as exc:
            QMessageBox.warning(self, self._window_title, f"Plot failed: {exc}")

    def _on_select_in_egg(self) -> None:
        self._select_region_oids(oids_in_egg_gia, region_label="egg")

    def _on_select_in_yolk(self) -> None:
        self._select_region_oids(oids_in_egg_yolk, region_label="yolk")

    def _on_select_in_triangle(self) -> None:
        self._select_region_oids(oids_in_golden_triangle_region, region_label="triangle")

    def _select_region_oids(self, picker: Callable[[MedChemSpaceDataset], list[int]], *, region_label: str) -> None:
        if self.parent_app is None:
            return
        if self._full_dataset is None or not self._full_dataset.points:
            QMessageBox.information(self, self._window_title, "Refresh the plot first.")
            return
        oids = picker(self._full_dataset)
        if not oids:
            QMessageBox.information(
                self,
                self._window_title,
                f"No compounds in the current scope fall within the {region_label} region.",
            )
            return
        extra = f"{self._window_title}: {region_label} region"
        n = self.parent_app.select_table_oids(oids, extra_status=extra)
        if self._plot_view is not None:
            self._plot_view.sync_from_table_selection()
        if n <= 0:
            QMessageBox.information(
                self,
                self._window_title,
                f"No table rows matched the {region_label} region selection.",
            )


class MedChemSpaceDialog(QDialog):
    """Floating window for a :class:`MedChemPlotPanel`."""

    def __init__(
        self,
        parent: ChemicalTableApp | None,
        *,
        plot_kind: MedChemPlotKind,
        window_title: str,
        panel: MedChemPlotPanel | None = None,
    ):
        super().__init__(parent)
        self.parent_app = parent
        self._plot_kind = plot_kind
        if panel is not None:
            self._panel = panel
            self._panel.setParent(self)
            self._panel.parent_app = parent
        else:
            self._panel = MedChemPlotPanel(parent, plot_kind=plot_kind, window_title=window_title)
        self.only_selected_cb = self._panel.only_selected_cb
        self._only_selected_scope_prefix = self._panel._only_selected_scope_prefix

        self.setWindowTitle(window_title)
        self.resize(920, 680)

        root = QVBoxLayout(self)
        root.addWidget(self._panel, 1)

        foot = QHBoxLayout()
        self._add_to_main_btn = QPushButton("Add to Main Window")
        self._add_to_main_btn.setToolTip("Dock this plot beside the compound table.")
        self._add_to_main_btn.clicked.connect(self._add_to_main_window)
        foot.addWidget(self._add_to_main_btn)
        foot.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        foot.addWidget(close_btn)
        root.addLayout(foot)

        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._force_close = False
        make_window_minimizable(self)

    def _add_to_main_window(self) -> None:
        if self.parent_app is None:
            return
        teardown = getattr(self, "_scope_sync_disconnect", None)
        if callable(teardown):
            teardown()
        if not self.parent_app.dock_plot_widget(self._panel):
            return
        self._panel = None
        self._force_close = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API name
        if self._force_close:
            self._force_close = False
        event.accept()
