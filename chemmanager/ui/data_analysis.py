"""Table data extraction and statistical analysis for the main window."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .qt_widget_utils import apply_monospace_to_text_edit

if TYPE_CHECKING:
    from .main_window import ChemicalTableApp


def table_to_dataframe(app: ChemicalTableApp, *, visible_only: bool = True) -> pd.DataFrame:
    """Build a DataFrame from the main table (skips Structure column)."""
    rows: list[dict[str, str]] = []
    m = app._table_model
    ncols = m.columnCount()
    nrows = m.rowCount()
    if not app.headers or ncols < 1 or nrows < 1:
        return pd.DataFrame()

    for r in range(nrows):
        if visible_only and app.table.isRowHidden(r):
            continue
        row: dict[str, str] = {}
        for c in range(ncols):
            if c >= len(app.headers):
                break
            name = app.headers[c]
            if name == "Structure":
                continue
            row[name] = (m.cell_text(r, c) or "").strip()
        rows.append(row)
    return pd.DataFrame(rows)


def numeric_subset(df: pd.DataFrame, *, exclude_id: bool = True) -> pd.DataFrame:
    """Columns that have at least one finite numeric value; optionally drop ID_HIDDEN."""
    if df.empty:
        return df
    cols = [c for c in df.columns if not (exclude_id and c == "ID_HIDDEN")]
    num = df[cols].apply(pd.to_numeric, errors="coerce")
    keep = [c for c in num.columns if num[c].notna().any()]
    return num[keep] if keep else pd.DataFrame(index=df.index)


class DataAnalysisDialog(QDialog):
    """Summarize, correlate, and explore numeric columns from the main table."""

    def __init__(self, parent: ChemicalTableApp | None = None):
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("Data — analyze table")
        self.resize(900, 640)
        self._df_raw = pd.DataFrame()
        self._df_num = pd.DataFrame()

        root = QVBoxLayout(self)
        scope = QHBoxLayout()
        self.chk_visible = QCheckBox("Visible rows only (respect filters)")
        self.chk_visible.setChecked(True)
        self.chk_visible.stateChanged.connect(self._reload)
        scope.addWidget(self.chk_visible)
        scope.addStretch()
        self.btn_refresh = QPushButton("Refresh from table")
        self.btn_refresh.clicked.connect(self._reload)
        scope.addWidget(self.btn_refresh)
        root.addLayout(scope)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self._tab_summary = self._build_summary_tab()
        self._tab_corr = self._build_correlation_tab()
        self._tab_percentiles = self._build_percentiles_tab()
        self._tab_regress = self._build_regression_tab()
        self.tabs.addTab(self._tab_summary, "Summary")
        self.tabs.addTab(self._tab_corr, "Correlations")
        self.tabs.addTab(self._tab_percentiles, "Percentiles")
        self.tabs.addTab(self._tab_regress, "Linear fit")

        bottom = QHBoxLayout()
        self.btn_export = QPushButton("Export numeric columns to CSV…")
        self.btn_export.clicked.connect(self._export_csv)
        bottom.addWidget(self.btn_export)
        bottom.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._reload()

    def _build_summary_tab(self) -> QWidget:
        w = QWidget()
        ly = QVBoxLayout(w)
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        apply_monospace_to_text_edit(self.summary_text)
        ly.addWidget(self.summary_text)
        btn = QPushButton("Copy summary to clipboard")
        btn.clicked.connect(self._copy_summary)
        ly.addWidget(btn)
        return w

    def _build_correlation_tab(self) -> QWidget:
        w = QWidget()
        ly = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Method:"))
        self.corr_method = QComboBox()
        self.corr_method.addItems(["pearson", "spearman", "kendall"])
        row.addWidget(self.corr_method)
        self.btn_corr = QPushButton("Compute correlation matrix")
        self.btn_corr.clicked.connect(self._run_correlation)
        row.addWidget(self.btn_corr)
        row.addStretch()
        ly.addLayout(row)
        self.corr_table = QTableWidget()
        self.corr_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.corr_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        ly.addWidget(self.corr_table)
        return w

    def _build_percentiles_tab(self) -> QWidget:
        w = QWidget()
        ly = QVBoxLayout(w)
        ly.addWidget(
            QLabel(
                "Enter percentiles as comma-separated numbers (e.g. 5, 25, 50, 75, 95). "
                "Uses linear interpolation between ranks."
            )
        )
        row = QHBoxLayout()
        self.percentile_input = QLineEdit("5, 25, 50, 75, 95")
        row.addWidget(self.percentile_input)
        self.btn_percentiles = QPushButton("Compute")
        self.btn_percentiles.clicked.connect(self._run_percentiles)
        row.addWidget(self.btn_percentiles)
        ly.addLayout(row)
        self.percentile_text = QTextEdit()
        self.percentile_text.setReadOnly(True)
        apply_monospace_to_text_edit(self.percentile_text)
        ly.addWidget(self.percentile_text)
        return w

    def _build_regression_tab(self) -> QWidget:
        w = QWidget()
        ly = QVBoxLayout(w)
        gb = QGroupBox("Ordinary least squares (single predictor)")
        form = QFormLayout(gb)
        self.reg_x = QComboBox()
        self.reg_y = QComboBox()
        form.addRow("X (predictor):", self.reg_x)
        form.addRow("Y (response):", self.reg_y)
        self.btn_regress = QPushButton("Fit line")
        self.btn_regress.clicked.connect(self._run_regression)
        form.addRow(self.btn_regress)
        ly.addWidget(gb)
        self.regress_text = QTextEdit()
        self.regress_text.setReadOnly(True)
        apply_monospace_to_text_edit(self.regress_text)
        ly.addWidget(self.regress_text)
        return w

    def _reload(self) -> None:
        if self.parent_app is None:
            return
        vis = self.chk_visible.isChecked()
        self._df_raw = table_to_dataframe(self.parent_app, visible_only=vis)
        self._df_num = numeric_subset(self._df_raw, exclude_id=True)
        self._populate_reg_combos()
        self._run_summary()
        self.regress_text.clear()
        self.corr_table.setRowCount(0)
        self.corr_table.setColumnCount(0)
        self.percentile_text.clear()

    def _populate_reg_combos(self) -> None:
        cols = list(self._df_num.columns)
        self.reg_x.clear()
        self.reg_y.clear()
        self.reg_x.addItems(cols)
        self.reg_y.addItems(cols)
        if len(cols) >= 2:
            self.reg_y.setCurrentIndex(1)

    def _run_summary(self) -> None:
        lines: list[str] = []
        n = len(self._df_raw)
        lines.append(f"Rows: {n}")
        lines.append(f"Columns (excl. Structure): {self._df_raw.shape[1]}")
        num_cols = list(self._df_num.columns)
        lines.append(f"Numeric columns (≥1 value): {len(num_cols)}")
        if num_cols:
            lines.append("")
            desc = self._df_num.describe(percentiles=[0.05, 0.25, 0.5, 0.75, 0.95])
            lines.append(desc.to_string())
            lines.append("")
            sk = self._df_num.skew(numeric_only=True)
            ku = self._df_num.kurtosis(numeric_only=True)
            lines.append("Skewness:")
            lines.append(sk.to_string())
            lines.append("")
            lines.append("Kurtosis (excess):")
            lines.append(ku.to_string())
        else:
            lines.append("")
            lines.append("No numeric columns detected after coercion — add numeric property columns or calculations.")
        miss = self._df_raw.isna().sum()
        if miss.any():
            lines.append("")
            lines.append("Missing / non-numeric cells (raw column counts as NaN after coercion):")
            lines.append(miss[miss > 0].to_string())
        self.summary_text.setPlainText("\n".join(lines))

    def _copy_summary(self) -> None:
        from PyQt5.QtWidgets import QApplication

        QApplication.clipboard().setText(self.summary_text.toPlainText())

    def _run_correlation(self) -> None:
        if self._df_num.shape[1] < 2:
            QMessageBox.information(
                self,
                "Correlations",
                "Need at least two numeric columns with data. Try Calculate Descriptors or Calculator first.",
            )
            self.corr_table.setRowCount(0)
            self.corr_table.setColumnCount(0)
            return
        method = self.corr_method.currentText()
        try:
            corr = self._df_num.corr(method=method, numeric_only=True)
        except Exception as e:
            QMessageBox.warning(self, "Correlations", str(e))
            return
        self._fill_matrix_table(self.corr_table, corr)

    def _fill_matrix_table(self, table: QTableWidget, mat: pd.DataFrame) -> None:
        table.clear()
        labels = [str(x) for x in mat.columns]
        n = len(labels)
        table.setRowCount(n)
        table.setColumnCount(n)
        table.setHorizontalHeaderLabels(labels)
        table.setVerticalHeaderLabels(labels)
        for i in range(n):
            for j in range(n):
                v = mat.iloc[i, j]
                item = QTableWidgetItem("" if pd.isna(v) else f"{float(v):.6g}")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(i, j, item)

    def _parse_percentiles(self, s: str) -> list[float]:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        out: list[float] = []
        for p in parts:
            v = float(p)
            if v > 1.0:
                v = v / 100.0
            if not (0.0 < v < 1.0):
                raise ValueError(f"Percentile out of (0,100) exclusive: {p}")
            out.append(v)
        return sorted(set(out))

    def _run_percentiles(self) -> None:
        if self._df_num.empty:
            self.percentile_text.setPlainText("No numeric columns.")
            return
        try:
            qs = self._parse_percentiles(self.percentile_input.text())
        except ValueError as e:
            QMessageBox.warning(self, "Percentiles", str(e))
            return
        try:
            res = self._df_num.quantile(q=qs, interpolation="linear")
        except Exception as e:
            QMessageBox.warning(self, "Percentiles", str(e))
            return
        buf = io.StringIO()
        res.to_csv(buf)
        self.percentile_text.setPlainText(buf.getvalue())

    def _run_regression(self) -> None:
        xn = self.reg_x.currentText()
        yn = self.reg_y.currentText()
        if not xn or not yn or xn == yn:
            self.regress_text.setPlainText("Pick two different numeric columns.")
            return
        x = pd.to_numeric(self._df_num[xn], errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(self._df_num[yn], errors="coerce").to_numpy(dtype=float)
        mask = np.isfinite(x) & np.isfinite(y)
        x, y = x[mask], y[mask]
        n = len(x)
        if n < 2:
            self.regress_text.setPlainText("Need at least two paired finite values.")
            return
        slope, intercept = np.polyfit(x, y, 1)
        y_hat = slope * x + intercept
        ss_res = float(np.sum((y - y_hat) ** 2))
        y_mean = float(np.mean(y))
        ss_tot = float(np.sum((y - y_mean) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        rmse = float(np.sqrt(ss_res / n))
        lines = [
            f"N (finite pairs): {n}",
            f"Model: {yn} = {slope:.8g} * ({xn}) + {intercept:.8g}",
            f"R²: {r2:.8g}",
            f"RMSE: {rmse:.8g}",
            "",
            "NumPy polyfit (degree 1); intercept and slope are ordinary least squares.",
        ]
        self.regress_text.setPlainText("\n".join(lines))

    def _export_csv(self) -> None:
        if self._df_num.empty:
            QMessageBox.information(self, "Export", "No numeric columns to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export numeric data", "", "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            id_col = None
            if "ID_HIDDEN" in self._df_raw.columns:
                id_col = pd.to_numeric(self._df_raw["ID_HIDDEN"], errors="coerce")
            out = self._df_num.copy()
            if id_col is not None and len(id_col) == len(out):
                out.insert(0, "ID_HIDDEN", id_col.values)
            out.to_csv(path, index=False)
        except Exception as e:
            QMessageBox.warning(self, "Export", str(e))
            return
        QMessageBox.information(self, "Export", f"Wrote {path}")
