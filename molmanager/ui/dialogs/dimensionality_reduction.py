"""PCA, t-SNE, and UMAP visualization dialogs (Data menu)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from rdkit import Chem

from ...dimensionality_reduction import DimensionReductionResult, is_fingerprint_bitcount_column
from ...workers import SIMILARITY_FP_TYPE_LABELS
from ...workers.dimensionality_reduction import DimensionReductionSignals, DimensionReductionWorker
from ..data_analysis import numeric_subset, table_to_dataframe
from ...plot_color import (
    PLOT_COLORSCALE_CHOICES,
    color_values_are_numeric,
    normalize_color_column,
    resolve_plot_colorscale,
)
from ..plot_color_range_controls import PlotColorRangeControls
from ..dimred_plot import build_dimension_reduction_figure, dimension_reduction_result_with_color
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

_FP_NONE_LABEL = "None"


class DimensionReductionPanel(QWidget):
    def __init__(self, parent: ChemicalTableApp | None, *, window_title: str, method: str):
        super().__init__(None)
        self.parent_app = parent
        self._method = method
        self._window_title = window_title
        n_sel = len(parent._selected_logical_rows()) if parent is not None else 0
        self._have_selection = n_sel > 0
        self._job_running = False
        self._last_result: DimensionReductionResult | None = None

        root = QVBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_ly = QVBoxLayout(left)
        left_ly.setContentsMargins(0, 0, 0, 0)

        src_grp = QGroupBox("Features")
        src_ly = QVBoxLayout(src_grp)
        self.column_list = QListWidget()
        self.column_list.setMinimumWidth(220)
        self.column_list.setMaximumHeight(200)
        src_ly.addWidget(self.column_list)

        fp_row = QHBoxLayout()
        fp_row.addWidget(QLabel("Fingerprint:"))
        self.fp_combo = QComboBox()
        self.fp_combo.addItem(_FP_NONE_LABEL)
        self.fp_combo.addItems(SIMILARITY_FP_TYPE_LABELS)
        self.fp_combo.setToolTip(
            "None: numeric columns only. Otherwise concatenate a 2D fingerprint bit vector."
        )
        self.fp_combo.currentIndexChanged.connect(self._on_fp_selection_changed)
        fp_row.addWidget(self.fp_combo, 1)
        src_ly.addLayout(fp_row)
        struct_row = QHBoxLayout()
        struct_row.addWidget(QLabel("Structure from:"))
        self.struct_src_combo = QComboBox()
        struct_row.addWidget(self.struct_src_combo, 1)
        src_ly.addLayout(struct_row)

        scope_row = QHBoxLayout()
        self.only_selected_cb = QCheckBox("Only Selected Rows")
        self._only_selected_scope_prefix = "Only Selected Rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({n_sel} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        self.only_selected_cb.stateChanged.connect(self._reload_columns)
        scope_row.addWidget(self.only_selected_cb)
        scope_row.addStretch()
        src_ly.addLayout(scope_row)

        left_ly.addWidget(src_grp)

        opts = QGroupBox("Options")
        self._opts_form = QFormLayout(opts)
        left_ly.addWidget(opts)
        self._build_method_options(self._opts_form)
        self.standardize_cb = QCheckBox("Standardize features (zero mean, unit variance)")
        self.standardize_cb.setChecked(True)
        self._opts_form.addRow(self.standardize_cb)

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
        self.colorscale_combo.currentIndexChanged.connect(self._on_color_range_or_scale_changed)
        color_row.addWidget(self.colorscale_combo)
        self.color_range = PlotColorRangeControls()
        self.color_range.connect_changed(self._on_color_range_changed)
        color_row.addWidget(self.color_range)
        color_row.addStretch()
        left_ly.addLayout(color_row)

        run_row = QHBoxLayout()
        run_row.setContentsMargins(0, 10, 0, 6)
        run_row.addStretch()
        self.run_btn = QPushButton(self._run_button_label())
        self.run_btn.clicked.connect(self._on_run)
        self.run_btn.setMinimumWidth(160)
        self.run_btn.setStyleSheet("QPushButton { padding: 8px 28px; }")
        run_row.addWidget(self.run_btn)
        run_row.addStretch()
        left_ly.addLayout(run_row)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumHeight(140)
        apply_monospace_to_text_edit(self.summary_text)
        left_ly.addWidget(QLabel("Results"))
        left_ly.addWidget(self.summary_text)
        splitter.addWidget(left)

        right = QWidget()
        right_ly = QVBoxLayout(right)
        right_ly.setContentsMargins(0, 0, 0, 0)
        if _HAS_WEB and parent is not None:
            self._plot_view = PlotlyInteractiveView(parent, right)
            self._plot_view.setMinimumHeight(320)
            right_ly.addWidget(self._plot_view, 1)
            self._plot_placeholder = None
        else:
            self._plot_view = None
            self._plot_placeholder = QLabel(
                "Install PyQtWebEngine to show the interactive plot in this window."
            )
            self._plot_placeholder.setWordWrap(True)
            self._plot_placeholder.setAlignment(Qt.AlignCenter)
            right_ly.addWidget(self._plot_placeholder, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        host = parent if parent is not None else self
        self._signals = DimensionReductionSignals(host)
        self._signals.finished.connect(self._on_finished)
        self._signals.failed.connect(self._on_failed)

        self._refresh_structure_sources()
        self._reload_columns()
        self._on_fp_selection_changed()
        self._update_spectrum_controls()

    def create_floating_dialog(self, parent_app: ChemicalTableApp) -> QDialog:
        """Re-open this panel in a floating window after undocking from the main table."""
        return _DIMRED_FLOATING_DIALOGS[self._method](parent_app, panel=self)

    def _run_button_label(self) -> str:
        labels = {"pca": "Run PCA", "tsne": "Run t-SNE", "umap": "Run UMAP"}
        return labels.get(self._method, "Run")

    def _build_method_options(self, form: QFormLayout) -> None:
        raise NotImplementedError

    def _method_params(self) -> dict:
        raise NotImplementedError

    def _use_fingerprints(self) -> bool:
        return self.fp_combo.currentText() != _FP_NONE_LABEL

    def _on_fp_selection_changed(self, *_args) -> None:
        use_fp = self._use_fingerprints()
        self.struct_src_combo.setEnabled(use_fp)
        if use_fp and not self._selected_feature_columns():
            self.standardize_cb.setToolTip(
                "When combined with fingerprints, only numeric columns are scaled; "
                "fingerprint bits are left unchanged."
            )
        else:
            self.standardize_cb.setToolTip("")

    def _refresh_structure_sources(self) -> None:
        self.struct_src_combo.clear()
        if self.parent_app is None:
            return
        self.struct_src_combo.addItems(self.parent_app.chemistry_tool_structure_sources())

    def _reload_columns(self) -> None:
        prev_color = self.color_combo.currentText()
        self.column_list.clear()
        self.color_combo.blockSignals(True)
        try:
            self.color_combo.clear()
            self.color_combo.addItem("(none)")
            if self.parent_app is None:
                return
            self._refresh_structure_sources()
            only_sel = selection_scope_checked(self)
            df, _rows = table_to_dataframe(self.parent_app, visible_only=True, only_selected=only_sel)
            num = numeric_subset(df, exclude_id=True)
            for col in num.columns:
                item = QListWidgetItem(col)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                self.column_list.addItem(item)
            for col in df.columns:
                if col != "ID_HIDDEN":
                    self.color_combo.addItem(col)
            idx = self.color_combo.findText(prev_color)
            if idx >= 0:
                self.color_combo.setCurrentIndex(idx)
        finally:
            self.color_combo.blockSignals(False)

    def _color_values_for_oids(self, oids: list[int], color_col: str | None) -> list[Any] | None:
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

    def _current_colorscale(self) -> str:
        return resolve_plot_colorscale(self.colorscale_combo.currentText())

    def _current_color_bounds(self) -> tuple[float | None, float | None]:
        return self.color_range.parse_bounds()

    def _update_spectrum_controls(self) -> None:
        enabled = self.color_combo.currentText() != "(none)"
        self._spectrum_label.setEnabled(enabled)
        self.colorscale_combo.setEnabled(enabled)
        numeric = False
        if enabled and self._last_result is not None:
            color_col = self.color_combo.currentText()
            vals = self._color_values_for_oids(self._last_result.oids, color_col)
            numeric = color_values_are_numeric(vals)
        self.color_range.set_enabled(enabled and numeric)

    def _on_color_range_changed(self) -> None:
        if self._last_result is None or self._job_running:
            return
        self._refresh_plot_colors()

    def _on_color_column_changed(self, _index: int = 0) -> None:
        self._update_spectrum_controls()
        if self._last_result is None or self._job_running:
            return
        self._refresh_plot_colors()

    def _on_color_range_or_scale_changed(self, *_args) -> None:
        if self._last_result is None or self._job_running:
            return
        self._refresh_plot_colors()

    def _refresh_plot_colors(self) -> None:
        if self._last_result is None or self._plot_view is None or self._job_running:
            return
        color_col = self.color_combo.currentText()
        if color_col == "(none)":
            color_col = None
        color_vals = self._color_values_for_oids(self._last_result.oids, color_col)
        color_vals, color_col = normalize_color_column(color_vals, color_col)
        updated = dimension_reduction_result_with_color(
            self._last_result,
            color_values=color_vals,
            color_label=color_col,
        )
        try:
            color_min, color_max = self._current_color_bounds()
            fig = build_dimension_reduction_figure(
                updated,
                colorscale=self._current_colorscale(),
                color_min=color_min,
                color_max=color_max,
            )
            self._plot_view.push_figure(fig, list(updated.oids))
        except Exception as exc:
            QMessageBox.warning(self, self._window_title, f"Plot failed: {exc}")

    def _selected_feature_columns(self) -> list[str]:
        cols: list[str] = []
        for i in range(self.column_list.count()):
            item = self.column_list.item(i)
            if item.checkState() == Qt.Checked:
                cols.append(item.text())
        return cols

    def _collect_table_mols(self, src: str, only_selected: bool) -> list[tuple[int, Chem.Mol]]:
        app = self.parent_app
        assert app is not None
        return app.collect_scoped_table_mols(
            src,
            only_selected=only_selected,
            only_visible=True,
        )

    def _scoped_dataframe_and_oids(self) -> tuple[pd.DataFrame, list[int]]:
        app = self.parent_app
        assert app is not None
        only_sel = selection_scope_checked(self)
        df, source_rows = table_to_dataframe(app, visible_only=True, only_selected=only_sel)
        oids: list[int] = []
        for r in source_rows:
            t0 = app._table_model.cell_text(r, 0)
            oids.append(int(t0) if t0.isdigit() else int(r))
        return df, oids

    def _on_run(self) -> None:
        if self._job_running or self.parent_app is None:
            return
        self._last_result = None
        use_fp = self._use_fingerprints()
        features = self._selected_feature_columns()
        only_sel = selection_scope_checked(self)
        if only_sel and not self.parent_app._selected_oids_set():
            QMessageBox.warning(
                self,
                self._window_title,
                "\u201cOnly Selected Rows\u201d is checked but nothing is selected.",
            )
            return
        try:
            df, oids = self._scoped_dataframe_and_oids()
        except Exception as exc:
            QMessageBox.warning(self, self._window_title, str(exc))
            return
        if df.empty:
            QMessageBox.information(self, self._window_title, "No rows in the current scope.")
            return
        if not features and not use_fp:
            QMessageBox.warning(
                self,
                self._window_title,
                "Select at least one numeric column and/or choose a fingerprint type.",
            )
            return
        if features and len(features) == 1 and is_fingerprint_bitcount_column(features[0]) and not use_fp:
            QMessageBox.warning(
                self,
                self._window_title,
                f"The column “{features[0]}” stores only the number of on-bits, not the full "
                "fingerprint vector.\n\n"
                "Choose a fingerprint type other than None, or select multiple numeric columns.",
            )
            return
        color_col = self.color_combo.currentText()
        if color_col == "(none)":
            color_col = None
        mol_rows = None
        if use_fp:
            src = self.struct_src_combo.currentText()
            mol_rows = self._collect_table_mols(src, only_sel)
            if len(mol_rows) < 2 and not features:
                QMessageBox.information(
                    self,
                    self._window_title,
                    "Need at least two rows with valid structures when using fingerprints alone.",
                )
                return
        params = {
            "method": self._method,
            "dataframe": df,
            "oids": oids,
            "feature_columns": features,
            "use_fingerprints": use_fp,
            "mol_rows": mol_rows,
            "fingerprint": self.fp_combo.currentText() if use_fp else None,
            "standardize": self.standardize_cb.isChecked(),
            "color_column": color_col,
            **self._method_params(),
        }
        self._job_running = True
        self.run_btn.setEnabled(False)
        self.summary_text.setPlainText("Computing…")
        from ..background_jobs import register_background_job

        self._bg_job_id = f"dimred-{id(self)}"
        register_background_job(self.parent_app, self._bg_job_id, f"{self._window_title}…")
        worker = DimensionReductionWorker(params, self._signals)
        self.parent_app.threadpool.start(worker)

    def _clear_dimred_background_job(self) -> None:
        job_id = getattr(self, "_bg_job_id", None)
        if job_id and self.parent_app is not None:
            from ..background_jobs import unregister_background_job

            unregister_background_job(self.parent_app, job_id)
        self._bg_job_id = None

    def _on_finished(self, result) -> None:
        self._clear_dimred_background_job()
        self._job_running = False
        self.run_btn.setEnabled(True)
        self.summary_text.setPlainText(result.summary)
        self._last_result = result
        if self._plot_view is None:
            return
        color_col = self.color_combo.currentText()
        if color_col == "(none)":
            color_col = None
        color_vals = self._color_values_for_oids(result.oids, color_col)
        color_vals, color_col = normalize_color_column(color_vals, color_col)
        plotted = dimension_reduction_result_with_color(
            result,
            color_values=color_vals,
            color_label=color_col,
        )
        try:
            color_min, color_max = self._current_color_bounds()
            fig = build_dimension_reduction_figure(
                plotted,
                colorscale=self._current_colorscale(),
                color_min=color_min,
                color_max=color_max,
            )
            self._plot_view.push_figure(fig, list(plotted.oids))
            self._update_spectrum_controls()
            if self.parent_app is not None:
                n = len(result.oids)
                self.parent_app.status_label.setText(
                    f"{self._window_title}: rendered {n:,} point(s). Lasso or click to select table rows."
                )
        except Exception as exc:
            QMessageBox.warning(self, self._window_title, f"Plot failed: {exc}")

    def _on_failed(self, message: str) -> None:
        self._clear_dimred_background_job()
        self._job_running = False
        self.run_btn.setEnabled(True)
        self.summary_text.setPlainText("")
        QMessageBox.warning(self, self._window_title, message or "Computation failed.")


class DimensionReductionDialog(QDialog):
    """Floating window hosting a :class:`DimensionReductionPanel`."""

    def __init__(
        self,
        parent: ChemicalTableApp | None,
        *,
        panel: DimensionReductionPanel,
    ):
        super().__init__(parent)
        self.parent_app = parent
        self._panel = panel
        self._panel.setParent(self)
        self._panel.parent_app = parent
        self.only_selected_cb = self._panel.only_selected_cb
        self._only_selected_scope_prefix = self._panel._only_selected_scope_prefix

        self.setWindowTitle(panel._window_title)
        self.resize(980, 720)

        root = QVBoxLayout(self)
        root.addWidget(self._panel, 1)

        foot = QHBoxLayout()
        add_main = QPushButton("Add to Main Window")
        add_main.setToolTip("Dock this plot beside the compound table.")
        add_main.clicked.connect(self._add_to_main_window)
        foot.addWidget(add_main)
        foot.addStretch()
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


class PCAPlotPanel(DimensionReductionPanel):
    """Principal component analysis on numeric table columns."""

    def __init__(self, parent: ChemicalTableApp | None = None):
        super().__init__(parent, window_title="Principal Component Analysis", method="pca")

    def _build_method_options(self, form: QFormLayout) -> None:
        self.pca_components = QSpinBox()
        self.pca_components.setRange(2, 50)
        self.pca_components.setValue(2)
        self.pca_components.setToolTip(
            "Number of principal components to compute (plot uses PC1 vs PC2)."
        )
        form.addRow("Components:", self.pca_components)

    def _method_params(self) -> dict:
        return {"n_components": int(self.pca_components.value())}


class PCADialog(DimensionReductionDialog):
    def __init__(self, parent: ChemicalTableApp | None = None, *, panel: DimensionReductionPanel | None = None):
        super().__init__(parent, panel=panel or PCAPlotPanel(parent))


class TSNEPlotPanel(DimensionReductionPanel):
    """t-SNE embedding of numeric table columns."""

    def __init__(self, parent: ChemicalTableApp | None = None):
        super().__init__(parent, window_title="t-SNE Visualization", method="tsne")

    def _build_method_options(self, form: QFormLayout) -> None:
        self.tsne_perplexity = QDoubleSpinBox()
        self.tsne_perplexity.setRange(5.0, 500.0)
        self.tsne_perplexity.setDecimals(1)
        self.tsne_perplexity.setValue(30.0)
        form.addRow("Perplexity:", self.tsne_perplexity)

        self.tsne_learning_rate = QDoubleSpinBox()
        self.tsne_learning_rate.setRange(10.0, 1000.0)
        self.tsne_learning_rate.setDecimals(0)
        self.tsne_learning_rate.setValue(200.0)
        form.addRow("Learning rate:", self.tsne_learning_rate)

        self.tsne_max_iter = QSpinBox()
        self.tsne_max_iter.setRange(250, 10000)
        self.tsne_max_iter.setSingleStep(250)
        self.tsne_max_iter.setValue(1000)
        form.addRow("Max iterations:", self.tsne_max_iter)

        self.tsne_max_points = QSpinBox()
        self.tsne_max_points.setRange(100, 50000)
        self.tsne_max_points.setValue(2500)
        self.tsne_max_points.setToolTip(
            "Subsample to this many rows when the table is larger (keeps the UI responsive)."
        )
        form.addRow("Max points:", self.tsne_max_points)

        self.tsne_seed = QSpinBox()
        self.tsne_seed.setRange(0, 999_999)
        self.tsne_seed.setValue(42)
        form.addRow("Random seed:", self.tsne_seed)

    def _method_params(self) -> dict:
        return {
            "perplexity": float(self.tsne_perplexity.value()),
            "learning_rate": float(self.tsne_learning_rate.value()),
            "max_iter": int(self.tsne_max_iter.value()),
            "max_points": int(self.tsne_max_points.value()),
            "random_state": int(self.tsne_seed.value()),
        }


class TSNEVisualizationDialog(DimensionReductionDialog):
    def __init__(self, parent: ChemicalTableApp | None = None, *, panel: DimensionReductionPanel | None = None):
        super().__init__(parent, panel=panel or TSNEPlotPanel(parent))


class UMAPPlotPanel(DimensionReductionPanel):
    """UMAP embedding of numeric table columns or fingerprints."""

    def __init__(self, parent: ChemicalTableApp | None = None):
        super().__init__(parent, window_title="UMAP Visualization", method="umap")

    def _build_method_options(self, form: QFormLayout) -> None:
        self.umap_neighbors = QSpinBox()
        self.umap_neighbors.setRange(2, 200)
        self.umap_neighbors.setValue(15)
        self.umap_neighbors.setToolTip(
            "Local neighborhood size; lower values emphasize fine structure, higher values global layout."
        )
        form.addRow("n_neighbors:", self.umap_neighbors)

        self.umap_min_dist = QDoubleSpinBox()
        self.umap_min_dist.setRange(0.0, 0.99)
        self.umap_min_dist.setDecimals(2)
        self.umap_min_dist.setSingleStep(0.05)
        self.umap_min_dist.setValue(0.1)
        self.umap_min_dist.setToolTip(
            "Minimum spacing between embedded points (0 = tight clusters, ~1 = spread out)."
        )
        form.addRow("min_dist:", self.umap_min_dist)

        self.umap_max_points = QSpinBox()
        self.umap_max_points.setRange(100, 50000)
        self.umap_max_points.setValue(2500)
        self.umap_max_points.setToolTip(
            "Subsample to this many rows when the table is larger (keeps the UI responsive)."
        )
        form.addRow("Max points:", self.umap_max_points)

        self.umap_seed = QSpinBox()
        self.umap_seed.setRange(0, 999_999)
        self.umap_seed.setValue(42)
        form.addRow("Random seed:", self.umap_seed)

    def _method_params(self) -> dict:
        return {
            "n_neighbors": int(self.umap_neighbors.value()),
            "min_dist": float(self.umap_min_dist.value()),
            "max_points": int(self.umap_max_points.value()),
            "random_state": int(self.umap_seed.value()),
        }


class UMAPVisualizationDialog(DimensionReductionDialog):
    def __init__(self, parent: ChemicalTableApp | None = None, *, panel: DimensionReductionPanel | None = None):
        super().__init__(parent, panel=panel or UMAPPlotPanel(parent))


_DIMRED_FLOATING_DIALOGS = {
    "pca": PCADialog,
    "tsne": TSNEVisualizationDialog,
    "umap": UMAPVisualizationDialog,
}
