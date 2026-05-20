"""PCA, t-SNE, and UMAP visualization dialogs (Data menu)."""

from __future__ import annotations

from typing import TYPE_CHECKING

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

from ...dimensionality_reduction import is_fingerprint_bitcount_column
from ...workers import SIMILARITY_FP_TYPE_LABELS
from ...workers.dimensionality_reduction import DimensionReductionSignals, DimensionReductionWorker
from ..data_analysis import numeric_subset, table_to_dataframe
from ..dimred_plot import build_dimension_reduction_figure
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


class DimensionReductionPanel(QWidget):
    def __init__(self, parent: ChemicalTableApp | None, *, window_title: str, method: str):
        super().__init__(None)
        self.parent_app = parent
        self._method = method
        self._window_title = window_title
        n_sel = len(parent._selected_logical_rows()) if parent is not None else 0
        self._have_selection = n_sel > 0
        self._job_running = False

        root = QVBoxLayout(self)
        scope = QHBoxLayout()
        self.chk_visible = QCheckBox("Visible rows only (respect filters)")
        self.chk_visible.setChecked(True)
        self.chk_visible.stateChanged.connect(self._reload_columns)
        scope.addWidget(self.chk_visible)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({n_sel} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        self.only_selected_cb.stateChanged.connect(self._reload_columns)
        scope.addWidget(self.only_selected_cb)
        scope.addStretch()
        self.btn_refresh_cols = QPushButton("Refresh columns")
        self.btn_refresh_cols.clicked.connect(self._reload_columns)
        scope.addWidget(self.btn_refresh_cols)
        root.addLayout(scope)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_ly = QVBoxLayout(left)
        left_ly.setContentsMargins(0, 0, 0, 0)

        src_grp = QGroupBox("Feature source")
        src_ly = QVBoxLayout(src_grp)
        self.feature_source_combo = QComboBox()
        self.feature_source_combo.addItems(
            [
                "Molecular fingerprint (from structure)",
                "Numeric table columns",
            ]
        )
        self.feature_source_combo.currentIndexChanged.connect(self._on_feature_source_changed)
        src_ly.addWidget(self.feature_source_combo)
        fp_row = QHBoxLayout()
        fp_row.addWidget(QLabel("Fingerprint:"))
        self.fp_combo = QComboBox()
        self.fp_combo.addItems(SIMILARITY_FP_TYPE_LABELS)
        fp_row.addWidget(self.fp_combo, 1)
        src_ly.addLayout(fp_row)
        struct_row = QHBoxLayout()
        struct_row.addWidget(QLabel("Structure from:"))
        self.struct_src_combo = QComboBox()
        struct_row.addWidget(self.struct_src_combo, 1)
        src_ly.addLayout(struct_row)
        self.fp_hint = QLabel(
            "Uses full bit vectors computed from structures (same as Cluster), not the "
            "on-bit count columns from Calculate Properties."
        )
        self.fp_hint.setWordWrap(True)
        self.fp_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        src_ly.addWidget(self.fp_hint)
        left_ly.addWidget(src_grp)

        col_grp = QGroupBox("Feature columns (numeric)")
        col_ly = QVBoxLayout(col_grp)
        self.column_list = QListWidget()
        self.column_list.setMinimumWidth(220)
        self.column_list.setMaximumHeight(200)
        col_ly.addWidget(self.column_list)
        col_btns = QHBoxLayout()
        btn_all = QPushButton("Select all")
        btn_all.clicked.connect(self._select_all_columns)
        self._btn_col_all = btn_all
        btn_none = QPushButton("Clear")
        btn_none.clicked.connect(self._clear_column_checks)
        self._btn_col_none = btn_none
        col_btns.addWidget(btn_all)
        col_btns.addWidget(btn_none)
        col_btns.addStretch()
        col_ly.addLayout(col_btns)
        left_ly.addWidget(col_grp)

        opts = QGroupBox("Options")
        self._opts_form = QFormLayout(opts)
        left_ly.addWidget(opts)
        self._build_method_options(self._opts_form)

        self.standardize_cb = QCheckBox("Standardize features (zero mean, unit variance)")
        self.standardize_cb.setChecked(True)
        left_ly.addWidget(self.standardize_cb)

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color by:"))
        self.color_combo = QComboBox()
        self.color_combo.setMinimumWidth(160)
        color_row.addWidget(self.color_combo, 1)
        left_ly.addLayout(color_row)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumHeight(140)
        apply_monospace_to_text_edit(self.summary_text)
        left_ly.addWidget(QLabel("Results"))
        left_ly.addWidget(self.summary_text)

        run_row = QHBoxLayout()
        self.run_btn = QPushButton(self._run_button_label())
        self.run_btn.clicked.connect(self._on_run)
        run_row.addWidget(self.run_btn)
        run_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        run_row.addWidget(close_btn)
        left_ly.addLayout(run_row)
        splitter.addWidget(left)

        right = QWidget()
        right_ly = QVBoxLayout(right)
        right_ly.setContentsMargins(0, 0, 0, 0)
        if _HAS_WEB and parent is not None:
            self._plot_view = PlotlyInteractiveView(parent, right)
            self._plot_view.setMinimumHeight(320)
            hint = QLabel("Click or lasso points to select rows in the table (same as Plotter).")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: palette(mid); font-size: 11px;")
            right_ly.addWidget(hint)
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
        self._on_feature_source_changed(self.feature_source_combo.currentIndex())

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

    def _on_feature_source_changed(self, _index: int) -> None:
        use_fp = self.feature_source_combo.currentIndex() == 0
        self.fp_combo.setEnabled(use_fp)
        self.struct_src_combo.setEnabled(use_fp)
        self.fp_hint.setVisible(use_fp)
        self.column_list.setEnabled(not use_fp)
        for btn in (getattr(self, "_btn_col_all", None), getattr(self, "_btn_col_none", None)):
            if btn is not None:
                btn.setEnabled(not use_fp)
        if use_fp:
            self.standardize_cb.setChecked(False)
            self.standardize_cb.setToolTip(
                "Scaling is usually not applied to binary fingerprints (Tanimoto geometry)."
            )
        else:
            self.standardize_cb.setToolTip("")

    def _refresh_structure_sources(self) -> None:
        self.struct_src_combo.clear()
        if self.parent_app is None:
            return
        self.struct_src_combo.addItems(self.parent_app.chemistry_tool_structure_sources())

    def _reload_columns(self) -> None:
        self.column_list.clear()
        self.color_combo.clear()
        self.color_combo.addItem("(none)")
        if self.parent_app is None:
            return
        self._refresh_structure_sources()
        vis = self.chk_visible.isChecked()
        only_sel = selection_scope_checked(self)
        df, _rows = table_to_dataframe(self.parent_app, visible_only=vis, only_selected=only_sel)
        num = numeric_subset(df, exclude_id=True)
        for col in num.columns:
            item = QListWidgetItem(col)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if num.shape[1] <= 8 else Qt.Unchecked)
            self.column_list.addItem(item)
        for col in df.columns:
            if col != "ID_HIDDEN":
                self.color_combo.addItem(col)

    def _select_all_columns(self) -> None:
        for i in range(self.column_list.count()):
            self.column_list.item(i).setCheckState(Qt.Checked)

    def _clear_column_checks(self) -> None:
        for i in range(self.column_list.count()):
            self.column_list.item(i).setCheckState(Qt.Unchecked)

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
            only_visible=self.chk_visible.isChecked(),
        )

    def _scoped_dataframe_and_oids(self) -> tuple[pd.DataFrame, list[int]]:
        app = self.parent_app
        assert app is not None
        vis = self.chk_visible.isChecked()
        only_sel = selection_scope_checked(self)
        df, source_rows = table_to_dataframe(app, visible_only=vis, only_selected=only_sel)
        oids: list[int] = []
        for r in source_rows:
            t0 = app._table_model.cell_text(r, 0)
            oids.append(int(t0) if t0.isdigit() else int(r))
        return df, oids

    def _on_run(self) -> None:
        if self._job_running or self.parent_app is None:
            return
        use_fp = self.feature_source_combo.currentIndex() == 0
        only_sel = selection_scope_checked(self)
        if only_sel and not self.parent_app._selected_oids_set():
            QMessageBox.warning(
                self,
                self._window_title,
                "\u201cOnly selected rows\u201d is checked but nothing is selected.",
            )
            return
        try:
            df, oids = self._scoped_dataframe_and_oids()
        except Exception as exc:
            QMessageBox.warning(self, self._window_title, str(exc))
            return
        if df.empty and not use_fp:
            QMessageBox.information(self, self._window_title, "No rows in the current scope.")
            return
        color_col = self.color_combo.currentText()
        if color_col == "(none)":
            color_col = None
        if use_fp:
            src = self.struct_src_combo.currentText()
            mol_rows = self._collect_table_mols(src, only_sel)
            if len(mol_rows) < 2:
                QMessageBox.information(
                    self,
                    self._window_title,
                    "Need at least two rows with valid structures in this scope.",
                )
                return
            params = {
                "method": self._method,
                "feature_source": "fingerprint",
                "mol_rows": mol_rows,
                "fingerprint": self.fp_combo.currentText(),
                "dataframe": df,
                "standardize": self.standardize_cb.isChecked(),
                "color_column": color_col,
                **self._method_params(),
            }
        else:
            features = self._selected_feature_columns()
            if not features:
                QMessageBox.warning(
                    self, self._window_title, "Select at least one numeric feature column."
                )
                return
            if len(features) == 1 and is_fingerprint_bitcount_column(features[0]):
                QMessageBox.warning(
                    self,
                    self._window_title,
                    f"The column “{features[0]}” stores only the number of on-bits, not the full "
                    "fingerprint vector.\n\n"
                    "Switch Feature source to “Molecular fingerprint (from structure)” "
                    "or select multiple numeric descriptor columns.",
                )
                return
            params = {
                "method": self._method,
                "feature_source": "columns",
                "dataframe": df,
                "oids": oids,
                "feature_columns": features,
                "standardize": self.standardize_cb.isChecked(),
                "color_column": color_col,
                **self._method_params(),
            }
        self._job_running = True
        self.run_btn.setEnabled(False)
        self.summary_text.setPlainText("Computing…")
        worker = DimensionReductionWorker(params, self._signals)
        self.parent_app.threadpool.start(worker)

    def _on_finished(self, result) -> None:
        self._job_running = False
        self.run_btn.setEnabled(True)
        self.summary_text.setPlainText(result.summary)
        if self._plot_view is None:
            return
        try:
            fig = build_dimension_reduction_figure(result)
            self._plot_view.push_figure(fig, list(result.oids))
            if self.parent_app is not None:
                n = len(result.oids)
                self.parent_app.status_label.setText(
                    f"{self._window_title}: rendered {n:,} point(s). Lasso or click to select table rows."
                )
        except Exception as exc:
            QMessageBox.warning(self, self._window_title, f"Plot failed: {exc}")

    def _on_failed(self, message: str) -> None:
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
        hint = QLabel("Linear projection; best for correlated numeric descriptors.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        form.addRow(hint)

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

        hint = QLabel(
            "Nonlinear embedding; can be slow on large tables. Increase max iterations for denser maps."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        form.addRow(hint)

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

        hint = QLabel(
            "Nonlinear embedding; often faster than t-SNE on large tables. "
            "For binary fingerprints, leave standardization off."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        form.addRow(hint)

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
