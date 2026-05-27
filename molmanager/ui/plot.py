"""Plot dialog: Plotly scatter linked to the main table."""

from __future__ import annotations

__all__ = [
    "AXIS_NONE",
    "PLOT_TYPE_SCATTER",
    "PLOT_TYPE_CHOICES",
    "PlotDialog",
    "PlotWidget",
    "compute_histogram_bin_edges",
    "infer_plot_mode",
    "normalize_axis_name",
    "oids_in_histogram_bin",
    "resolve_plot_mode",
]

AXIS_NONE = "None"

PLOT_TYPE_SCATTER = "scatter"
PLOT_TYPE_LINE_2D = "line_2d"
PLOT_TYPE_BOX = "box"
PLOT_TYPE_VIOLIN = "violin"
PLOT_TYPE_HEATMAP = "heatmap"

PLOT_TYPE_CHOICES: tuple[tuple[str, str], ...] = (
    ("Scatter/Histogram", PLOT_TYPE_SCATTER),
    ("2D Line", PLOT_TYPE_LINE_2D),
    ("Heatmap", PLOT_TYPE_HEATMAP),
    ("Box plot", PLOT_TYPE_BOX),
    ("Violin", PLOT_TYPE_VIOLIN),
)

import json
import math
import tempfile
import time
import webbrowser
from pathlib import Path

from PyQt5.QtCore import QObject, Qt, QTimer, QUrl, pyqtSlot
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QShortcut,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from plotly import graph_objects as go

from ..plot_analysis import (
    FIT_NONE,
    FIT_TRUNCATED_GAUSSIAN,
    HISTOGRAM_ONLY_FITS,
    PLOT_FIT_CHOICES,
    PLOT_FIT_CHOICES_XY,
    fit_histogram_curve,
    fit_xy_curve,
    summarize_univariate,
    summarize_xy,
)
from ..plot_color import (
    PLOT_COLORSCALE_CHOICES,
    color_values_are_numeric,
    normalize_color_column,
    resolve_plot_colorscale,
    scatter_marker_from_column_values,
)
from ..medchem_space import snapshot_scope_row_indices
from ..plot_heatmap import build_heatmap_figure, oids_in_heatmap_cell, summarize_heatmap
from .plot_color_range_controls import PlotColorRangeControls
from .plot_table_sync import (
    apply_table_selection_for_source_rows,
    clear_table_selection_from_plot,
    point_indices_for_oids,
    source_rows_for_point_indices,
)
from ..utils import safe_float
from .qt_widget_utils import apply_monospace_to_text_edit, make_window_minimizable


def normalize_axis_name(text: str | None) -> str | None:
    """Return a column name, or None when the combo is unset or ``AXIS_NONE``."""
    if not text:
        return None
    name = text.strip()
    if not name or name == AXIS_NONE:
        return None
    return name


def infer_plot_mode(x: str | None, y: str | None, z: str | None) -> str | None:
    """Infer plot type from axis combo text: Histogram, 2D, 3D, or None if invalid."""
    xn = normalize_axis_name(x)
    yn = normalize_axis_name(y)
    zn = normalize_axis_name(z)
    if not xn:
        return None
    if yn is None and zn is None:
        return "Histogram"
    if yn is not None and zn is None:
        return "2D"
    if yn is not None and zn is not None:
        return "3D"
    return None


def resolve_plot_mode(
    plot_type: str,
    x: str | None,
    y: str | None,
    z: str | None,
) -> str | None:
    """Resolve scatter/histogram mode from plot type and axis selections."""
    xn = normalize_axis_name(x)
    yn = normalize_axis_name(y)
    zn = normalize_axis_name(z)
    if plot_type == PLOT_TYPE_SCATTER:
        return infer_plot_mode(x, y, z)
    if plot_type == PLOT_TYPE_LINE_2D:
        return "2D" if xn and yn else None
    if plot_type == PLOT_TYPE_HEATMAP:
        return "Heatmap" if xn and yn else None
    return None


def compute_histogram_bin_edges(
    vals: list[float],
    *,
    bin_width: float | None = None,
    xmin: float | None = None,
    xmax: float | None = None,
) -> tuple[list[float], float]:
    """Return histogram bin edges (length n+1) and the bin width used."""
    if not vals:
        return [0.0, 1.0], 1.0
    lo = float(xmin) if xmin is not None else min(vals)
    hi = float(xmax) if xmax is not None else max(vals)
    if lo > hi:
        lo, hi = hi, lo
    if math.isclose(lo, hi, rel_tol=0.0, abs_tol=1e-12):
        hi = lo + 1.0
    if bin_width is not None and bin_width > 0:
        width = float(bin_width)
        start = math.floor(lo / width) * width
        end = math.ceil(hi / width) * width
        edges = []
        x = start
        guard = 0
        while x <= end + abs(end) * 1e-9 and guard < 10_000:
            edges.append(x)
            x += width
            guard += 1
        if len(edges) < 2:
            edges = [lo, lo + width]
        return edges, width
    n = len(vals)
    n_bins = max(1, int(math.ceil(math.log2(n) + 1))) if n else 1
    width = (hi - lo) / n_bins
    edges = [lo + i * width for i in range(n_bins + 1)]
    edges[-1] = hi
    return edges, width


def oids_at_histogram_point_indices(oids: list[int], indices: list[int]) -> list[int]:
    """Map Plotly histogram ``pointNumbers`` indices to row OIDs (deduped, order preserved)."""
    n = len(oids)
    seen: set[int] = set()
    selected: list[int] = []
    for raw in indices:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if not (0 <= i < n):
            continue
        oid = int(oids[i])
        if oid in seen:
            continue
        seen.add(oid)
        selected.append(oid)
    return selected


def oids_in_histogram_bin(
    vals: list[float],
    oids: list[int],
    edges: list[float],
    bin_index: int,
) -> list[int]:
    """OIDs whose values fall in histogram bin ``bin_index`` (half-open, last bin inclusive)."""
    if bin_index < 0 or bin_index + 1 >= len(edges):
        return []
    lo, hi = float(edges[bin_index]), float(edges[bin_index + 1])
    last_bin = bin_index == len(edges) - 2
    selected: list[int] = []
    for v, oid in zip(vals, oids):
        vf = float(v)
        if last_bin:
            if lo <= vf <= hi:
                selected.append(int(oid))
        elif lo <= vf < hi:
            selected.append(int(oid))
    return selected


class _PlotBridge(QObject):
    """Bridge JS Plotly events back to Qt."""

    def __init__(self, plot_widget: "PlotWidget") -> None:
        super().__init__(plot_widget)
        self._plot_widget = plot_widget

    @pyqtSlot(int)
    def pointClicked(self, point_index: int) -> None:  # noqa: N802
        self._plot_widget._on_plot_point_clicked(int(point_index))

    @pyqtSlot(str)
    def pointsSelected(self, points_json: str) -> None:  # noqa: N802
        self._plot_widget._on_plot_points_selected(points_json)

    @pyqtSlot(str)
    def histogramPointsSelected(self, indices_json: str) -> None:  # noqa: N802
        self._plot_widget._on_histogram_points_selected(indices_json)

    @pyqtSlot(int)
    def histogramBinClicked(self, bin_index: int) -> None:  # noqa: N802
        self._plot_widget._on_histogram_bin_clicked(int(bin_index))

    @pyqtSlot(float, float)
    def heatmapCellClicked(self, x_value: float, y_value: float) -> None:  # noqa: N802
        self._plot_widget._on_heatmap_cell_clicked(float(x_value), float(y_value))

class PlotStatisticsPanel(QWidget):
    """Embedded statistics and curve-fit summary beside the plot options."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(200)
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 0, 0, 0)
        root.setSpacing(4)
        title = QLabel("Statistics")
        title.setStyleSheet("font-weight: bold;")
        root.addWidget(title)
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMinimumHeight(120)
        apply_monospace_to_text_edit(self.summary_text)
        root.addWidget(self.summary_text, 1)
        self.set_lines(["Plot data to see summary statistics."])

    def set_lines(self, lines: list[str]) -> None:
        self.summary_text.setPlainText("\n".join(lines) if lines else "")


class PlotWidget(QWidget):
    """Interactive Plotly plotter for numeric table columns (dialog or main-window panel)."""

    def __init__(self, parent_app=None):
        super().__init__(None)
        self.parent_app = parent_app

        self._plot_shell_path = Path(tempfile.gettempdir()) / f"MOLMANAGER_plot_shell_{id(self)}.html"
        self._last_browser_opened_path: str | None = None
        self._plotted_oids: list[int] = []
        self._selected_point_indices: set[int] = set()
        self._web_ready = False
        self._pending_table_selection_sync = False
        self._pending_payload_json: str | None = None
        self._prev_range_axis: dict[str, str] = {"x": "", "y": "", "z": ""}
        self._ignore_plot_clear_until: float = 0.0
        self._hist_edges: list[float] = []
        self._hist_vals: list[float] = []
        self._hist_oids: list[int] = []
        self._heat_x: list[float] = []
        self._heat_y: list[float] = []
        self._heat_oids: list[int] = []
        self._heat_x_edges: list[float] = []
        self._heat_y_edges: list[float] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        type_row = QHBoxLayout()
        type_row.setSpacing(6)
        type_row.addWidget(QLabel("Plot type:"))
        self.plot_type_combo = QComboBox()
        for label, key in PLOT_TYPE_CHOICES:
            self.plot_type_combo.addItem(label, key)
        self.plot_type_combo.setToolTip(
            "Scatter/Histogram: histogram when only X is set; 2D/3D scatter from axes. "
            "Other types use a fixed chart style."
        )
        type_row.addWidget(self.plot_type_combo, 1)
        root.addLayout(type_row)

        self.web = QWebEngineView(self)
        self.web.setMinimumHeight(220)
        self.web.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.web, 1)

        ctrl_wrap = QWidget(self)
        ctrl_root = QVBoxLayout(ctrl_wrap)
        ctrl_root.setContentsMargins(0, 0, 0, 0)
        ctrl_root.setSpacing(4)

        range_edit_w = 76
        self.x_combo = QComboBox()
        self.y_combo = QComboBox()
        self.z_combo = QComboBox()
        self.xmin = QLineEdit()
        self.xmin.setFixedWidth(range_edit_w)
        self.xmax = QLineEdit()
        self.xmax.setFixedWidth(range_edit_w)
        self.ymin = QLineEdit()
        self.ymin.setFixedWidth(range_edit_w)
        self.ymax = QLineEdit()
        self.ymax.setFixedWidth(range_edit_w)
        self.zmin = QLineEdit()
        self.zmin.setFixedWidth(range_edit_w)
        self.zmax = QLineEdit()
        self.zmax.setFixedWidth(range_edit_w)
        self.hist_bin_width = QLineEdit()
        self.hist_bin_width.setFixedWidth(range_edit_w)
        self.hist_bin_width.setToolTip("X-axis bin width for histogram or heatmap (empty = automatic).")
        self.hist_bin_width.editingFinished.connect(self._schedule_plot)
        self.heatmap_y_bin_width_label = QLabel("Y bin width:")
        self.heatmap_y_bin_width = QLineEdit()
        self.heatmap_y_bin_width.setFixedWidth(range_edit_w)
        self.heatmap_y_bin_width.setToolTip("Y-axis bin width for heatmap (empty = automatic).")
        self.heatmap_y_bin_width.editingFinished.connect(self._schedule_plot)

        gb_axes = QGroupBox("Axes")
        axes_l = QVBoxLayout(gb_axes)
        axes_l.setSpacing(4)

        self.hist_bin_width_label = QLabel("Bin width:")
        self._x_axis_row = self._build_axis_row(
            "X",
            self.x_combo,
            self.xmin,
            self.xmax,
            extra_after_range=(self.hist_bin_width_label, self.hist_bin_width),
        )
        self._y_axis_row = self._build_axis_row(
            "Y",
            self.y_combo,
            self.ymin,
            self.ymax,
            extra_after_range=(self.heatmap_y_bin_width_label, self.heatmap_y_bin_width),
        )
        self._z_axis_row = self._build_axis_row("Z", self.z_combo, self.zmin, self.zmax)
        axes_l.addWidget(self._x_axis_row)
        axes_l.addWidget(self._y_axis_row)
        axes_l.addWidget(self._z_axis_row)

        cols = self._numeric_column_names()
        self.x_combo.addItems(cols)
        self._populate_optional_axis_combo(self.y_combo, cols, AXIS_NONE)
        self._populate_optional_axis_combo(self.z_combo, cols, AXIS_NONE)

        n_sel = len(parent_app._selected_logical_rows()) if parent_app is not None else 0
        self._plot_scope_has_selection = n_sel > 0
        self.only_selected_cb = QCheckBox("Plot only selected rows")
        self._only_selected_scope_prefix = "Plot only selected rows"
        if self._plot_scope_has_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({n_sel} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)

        self.only_selected_cb.stateChanged.connect(self._schedule_plot)
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        actions_row.addWidget(self.only_selected_cb)
        actions_row.addStretch()
        axes_l.addLayout(actions_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        self._color_by_label = QLabel("Color by:")
        color_row.addWidget(self._color_by_label)
        self.color_combo = QComboBox()
        self.color_combo.setMinimumWidth(120)
        self.color_combo.setToolTip(
            "Color scatter and line points by a table column (numeric or categorical)."
        )
        self.color_combo.currentIndexChanged.connect(self._on_color_column_changed)
        color_row.addWidget(self.color_combo)
        self._spectrum_label = QLabel("Spectrum:")
        color_row.addWidget(self._spectrum_label)
        self.colorscale_combo = QComboBox()
        self.colorscale_combo.setMinimumWidth(100)
        self.colorscale_combo.addItems(PLOT_COLORSCALE_CHOICES)
        self.colorscale_combo.setToolTip("Continuous colorscale for numeric Color by columns.")
        self.colorscale_combo.currentIndexChanged.connect(self._schedule_plot)
        color_row.addWidget(self.colorscale_combo)
        self.color_range = PlotColorRangeControls()
        self.color_range.connect_changed(self._schedule_plot)
        color_row.addWidget(self.color_range)
        color_row.addStretch()
        axes_l.addLayout(color_row)

        analysis_row = QHBoxLayout()
        analysis_row.setSpacing(6)
        self._fit_label = QLabel("Fit:")
        analysis_row.addWidget(self._fit_label)
        self.fit_combo = QComboBox()
        self.fit_combo.setMinimumWidth(120)
        self.fit_combo.setToolTip(
            "Overlay a fit on 2D/line plots, or on histograms (Gaussian, log-normal, truncated Gaussian, or bin trends)."
        )
        self._refresh_fit_combo_items()
        self.fit_combo.currentIndexChanged.connect(self._on_fit_option_changed)
        analysis_row.addWidget(self.fit_combo)
        trunc_bound_w = 64
        self._trunc_lower_label = QLabel("Trunc lower:")
        self._trunc_lower_label.setToolTip("Lower truncation bound (empty = no lower bound).")
        analysis_row.addWidget(self._trunc_lower_label)
        self.fit_trunc_lower = QLineEdit()
        self.fit_trunc_lower.setFixedWidth(trunc_bound_w)
        self.fit_trunc_lower.setPlaceholderText("—")
        self.fit_trunc_lower.setToolTip(self._trunc_lower_label.toolTip())
        self.fit_trunc_lower.editingFinished.connect(self._schedule_plot)
        analysis_row.addWidget(self.fit_trunc_lower)
        self._trunc_upper_label = QLabel("Trunc upper:")
        self._trunc_upper_label.setToolTip("Upper truncation bound (empty = no upper bound).")
        analysis_row.addWidget(self._trunc_upper_label)
        self.fit_trunc_upper = QLineEdit()
        self.fit_trunc_upper.setFixedWidth(trunc_bound_w)
        self.fit_trunc_upper.setPlaceholderText("—")
        self.fit_trunc_upper.setToolTip(self._trunc_upper_label.toolTip())
        self.fit_trunc_upper.editingFinished.connect(self._schedule_plot)
        analysis_row.addWidget(self.fit_trunc_upper)
        analysis_row.addStretch()
        axes_l.addLayout(analysis_row)

        ctrl_root.addWidget(gb_axes)

        bottom_splitter = QSplitter(Qt.Horizontal)
        bottom_splitter.setChildrenCollapsible(False)
        bottom_splitter.addWidget(ctrl_wrap)
        self._stats_panel = PlotStatisticsPanel(self)
        bottom_splitter.addWidget(self._stats_panel)
        bottom_splitter.setStretchFactor(0, 1)
        bottom_splitter.setStretchFactor(1, 1)
        bottom_splitter.setSizes([400, 280])
        root.addWidget(bottom_splitter)

        self._bridge = _PlotBridge(self)
        self._web_channel = QWebChannel(self.web.page())
        self._web_channel.registerObject("chemBridge", self._bridge)
        self.web.page().setWebChannel(self._web_channel)
        self.web.loadFinished.connect(self._on_web_load_finished)

        self._plot_debounce = QTimer(self)
        self._plot_debounce.setSingleShot(True)
        self._plot_debounce.timeout.connect(self.plot)

        self.plot_type_combo.currentIndexChanged.connect(self._on_plot_type_change)
        self.x_combo.currentIndexChanged.connect(self._on_axis_change)
        self.y_combo.currentIndexChanged.connect(self._on_axis_change)
        self.z_combo.currentIndexChanged.connect(self._on_axis_change)
        self.xmin.editingFinished.connect(self._schedule_plot)
        self.xmax.editingFinished.connect(self._schedule_plot)
        self.ymin.editingFinished.connect(self._schedule_plot)
        self.ymax.editingFinished.connect(self._schedule_plot)
        self.zmin.editingFinished.connect(self._schedule_plot)
        self.zmax.editingFinished.connect(self._schedule_plot)

        self._load_plot_shell()
        self._reload_color_columns()
        self._on_axis_change()
        if parent_app is not None:
            model = parent_app._table_model
            model.rowsRemoved.connect(self._on_table_rows_changed)
            model.rowsInserted.connect(self._on_table_rows_changed)
            model.dataChanged.connect(self._on_table_data_changed)
            model.modelReset.connect(self._on_table_model_reset)
            model.columnsInserted.connect(self._on_table_columns_changed)
            model.columnsRemoved.connect(self._on_table_columns_changed)
            model.headerDataChanged.connect(self._on_table_header_data_changed)

    def _on_table_rows_changed(self, *_args) -> None:
        """Refresh plot when rows are added or removed."""
        if self._selected_point_indices:
            self._clear_plot_table_selection(update_plot=False)
        self._schedule_plot()

    def _on_table_data_changed(self, *_args) -> None:
        """Refresh plot when cell values change (filters, descriptors, edits)."""
        self._schedule_plot()

    def _on_table_model_reset(self, *_args) -> None:
        """Rows and columns may both change after a model reset."""
        self._on_table_columns_changed()

    def _on_table_columns_changed(self, *_args) -> None:
        """Repopulate axis combos when columns are inserted or removed."""
        self.refresh_axis_columns()

    def _on_table_header_data_changed(self, orientation, first: int, last: int) -> None:
        if int(orientation) == Qt.Horizontal:
            self.refresh_axis_columns()

    def refresh_axis_columns(self) -> None:
        """Sync X/Y/Z column lists with the table (e.g. after add/remove/rename or new numeric columns)."""
        if self.parent_app is None:
            return
        cols = self._numeric_column_names()
        x_prev = self.x_combo.currentText()
        y_prev = self.y_combo.currentText()
        z_prev = self.z_combo.currentText()
        self._set_axis_combo_items(self.x_combo, cols, previous=x_prev, allow_none=False)
        self._set_axis_combo_items(self.y_combo, cols, previous=y_prev, allow_none=True)
        self._set_axis_combo_items(self.z_combo, cols, previous=z_prev, allow_none=True)
        self._prev_range_axis = {"x": "", "y": "", "z": ""}
        self._reload_color_columns()
        self._on_axis_change()

    def _reload_color_columns(self) -> None:
        prev = self.color_combo.currentText()
        self.color_combo.blockSignals(True)
        try:
            self.color_combo.clear()
            self.color_combo.addItem("(none)")
            if self.parent_app is not None:
                for h in self.parent_app.headers:
                    if h and h != "ID_HIDDEN":
                        self.color_combo.addItem(h)
            idx = self.color_combo.findText(prev)
            if idx >= 0:
                self.color_combo.setCurrentIndex(idx)
        finally:
            self.color_combo.blockSignals(False)

    def _current_color_column(self) -> str | None:
        text = self.color_combo.currentText()
        return None if not text or text == "(none)" else text

    def _color_values_for_oids(self, oids: list[int]) -> tuple[list | None, str | None]:
        color_col = self._current_color_column()
        if not color_col or self.parent_app is None:
            return None, None
        model = self.parent_app._table_model
        out: list = []
        for oid in oids:
            row = self.parent_app.get_row_by_id(int(oid))
            if row < 0:
                out.append(None)
                continue
            raw = model.value_for_header(row, color_col)
            out.append(raw if (raw or "").strip() else None)
        return out, color_col

    def _scatter_marker_for_oids(self, oids: list[int], *, point_size: float = 6) -> dict:
        color_vals, color_label = self._color_values_for_oids(oids)
        color_vals, color_label = normalize_color_column(color_vals, color_label)
        color_min, color_max = self.color_range.parse_bounds()
        return scatter_marker_from_column_values(
            color_vals,
            color_label=color_label,
            colorscale=resolve_plot_colorscale(self.colorscale_combo.currentText()),
            color_min=color_min,
            color_max=color_max,
            point_size=point_size,
        )

    def _on_color_column_changed(self, _index: int = 0) -> None:
        self._update_color_controls()
        self._schedule_plot()

    def _current_fit_kind(self) -> str:
        key = self.fit_combo.currentData()
        return str(key) if key else FIT_NONE

    def _is_histogram_plot(self) -> bool:
        return (
            self._current_plot_type() == PLOT_TYPE_SCATTER
            and self._effective_plot_mode() == "Histogram"
        )

    def _analysis_supports_fit(self) -> bool:
        ptype = self._current_plot_type()
        if ptype in (PLOT_TYPE_HEATMAP, PLOT_TYPE_BOX, PLOT_TYPE_VIOLIN):
            return False
        if ptype == PLOT_TYPE_LINE_2D:
            return True
        if ptype != PLOT_TYPE_SCATTER:
            return False
        return self._effective_plot_mode() in ("2D", "Histogram")

    def _refresh_fit_combo_items(self) -> None:
        prev = self._current_fit_kind()
        choices = PLOT_FIT_CHOICES if self._is_histogram_plot() else PLOT_FIT_CHOICES_XY
        self.fit_combo.blockSignals(True)
        try:
            self.fit_combo.clear()
            for label, key in choices:
                self.fit_combo.addItem(label, key)
            idx = self.fit_combo.findData(prev)
            if idx < 0:
                idx = 0
            self.fit_combo.setCurrentIndex(idx)
        finally:
            self.fit_combo.blockSignals(False)

    def _on_fit_option_changed(self, *_args) -> None:
        self._update_analysis_controls()
        self._schedule_plot()

    def _truncation_bounds(self) -> tuple[float | None, float | None]:
        return (
            self._parse_edit_float(self.fit_trunc_lower),
            self._parse_edit_float(self.fit_trunc_upper),
        )

    def _uses_truncated_gaussian_fit(self) -> bool:
        return self._is_histogram_plot() and self._current_fit_kind() == FIT_TRUNCATED_GAUSSIAN

    def _present_analysis_panel(
        self,
        stats_lines: list[str] | None,
        fit_summary: str | None,
    ) -> None:
        parts: list[str] = []
        if stats_lines:
            parts.extend(stats_lines)
        if fit_summary:
            if parts:
                parts.append("")
            parts.append("Curve fit")
            parts.append(f"  {fit_summary}")
            parts.append("  (orange line on plot)")
        if not parts:
            self._stats_panel.set_lines(["No statistics for the current plot."])
            return
        self._stats_panel.set_lines(parts)

    def _present_analysis_dialog(
        self,
        stats_lines: list[str] | None,
        fit_summary: str | None,
    ) -> None:
        """Backward-compatible alias for :meth:`_present_analysis_panel`."""
        self._present_analysis_panel(stats_lines, fit_summary)

    def _fit_summary_from_name(self, name: str) -> str:
        return name.removeprefix("Fit: ").strip() if name.startswith("Fit:") else name

    def _add_fit_line_trace(self, fig: go.Figure, xs: list[float], ys: list[float]) -> None:
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                name="Fit",
                line={"color": "#e76f51", "width": 2.5, "dash": "solid"},
            )
        )

    def _add_xy_fit_trace(self, fig: go.Figure, x: list[float], y: list[float]) -> str | None:
        if self._is_histogram_plot() or self._current_fit_kind() in HISTOGRAM_ONLY_FITS:
            return None
        if not self._analysis_supports_fit() or self._current_fit_kind() == FIT_NONE:
            return None
        fit = fit_xy_curve(x, y, self._current_fit_kind())
        if not fit:
            return None
        xs, ys, name = fit
        self._add_fit_line_trace(fig, xs, ys)
        return self._fit_summary_from_name(name)

    def _add_histogram_fit_trace(
        self,
        fig: go.Figure,
        vals: list[float],
        edges: list[float],
        *,
        bin_width: float,
    ) -> str | None:
        if not self._is_histogram_plot() or self._current_fit_kind() == FIT_NONE:
            return None
        trunc_lo, trunc_hi = self._truncation_bounds()
        fit = fit_histogram_curve(
            vals,
            edges,
            self._current_fit_kind(),
            bin_width=bin_width,
            trunc_lower=trunc_lo,
            trunc_upper=trunc_hi,
        )
        if not fit:
            return None
        xs, ys, name = fit
        self._add_fit_line_trace(fig, xs, ys)
        return self._fit_summary_from_name(name)

    def _update_analysis_controls(self) -> None:
        self._refresh_fit_combo_items()
        fit_ok = self._analysis_supports_fit()
        self._fit_label.setEnabled(fit_ok)
        self.fit_combo.setEnabled(fit_ok)
        trunc_on = fit_ok and self._uses_truncated_gaussian_fit()
        for widget in (
            self._trunc_lower_label,
            self.fit_trunc_lower,
            self._trunc_upper_label,
            self.fit_trunc_upper,
        ):
            widget.setEnabled(trunc_on)
            widget.setVisible(self._is_histogram_plot())

    def _color_by_supported(self) -> bool:
        ptype = self._current_plot_type()
        if ptype in (PLOT_TYPE_HEATMAP, PLOT_TYPE_BOX, PLOT_TYPE_VIOLIN):
            return False
        if ptype == PLOT_TYPE_LINE_2D:
            return True
        if ptype != PLOT_TYPE_SCATTER:
            return False
        return self._effective_plot_mode() in ("2D", "3D")

    def _probe_color_values(self) -> list | None:
        """Sample color-column values for numeric vs categorical detection."""
        color_col = self._current_color_column()
        if not color_col or self.parent_app is None:
            return None
        if self._plotted_oids:
            vals, _ = self._color_values_for_oids(self._plotted_oids)
            return vals
        model = self.parent_app._table_model
        out: list = []
        for r in range(min(model.rowCount(), 2000)):
            raw = model.value_for_header(r, color_col)
            out.append(raw if (raw or "").strip() else None)
        return out

    def _update_color_controls(self) -> None:
        heatmap = self._is_heatmap_plot()
        supported = self._color_by_supported()
        self.color_combo.setEnabled(supported)
        self._color_by_label.setEnabled(supported)
        spectrum_on = heatmap or (supported and self._current_color_column() is not None)
        self._spectrum_label.setEnabled(spectrum_on)
        self.colorscale_combo.setEnabled(spectrum_on)
        numeric = (
            not heatmap
            and spectrum_on
            and color_values_are_numeric(self._probe_color_values())
        )
        self.color_range.set_enabled(numeric)
        self._update_analysis_controls()

    def _schedule_plot(self) -> None:
        self._plot_debounce.start(70)

    @staticmethod
    def _build_axis_row(
        axis_label: str,
        combo: QComboBox,
        edit_min: QLineEdit,
        edit_max: QLineEdit,
        *,
        extra_after_range: tuple[QWidget, QWidget] | None = None,
    ) -> QWidget:
        """One axis row: column combo with min/max range edits on the same line."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        axis_lbl = QLabel(f"{axis_label}:")
        axis_lbl.setMinimumWidth(14)
        lay.addWidget(axis_lbl)
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(combo, 1)
        lay.addWidget(QLabel("Min:"))
        lay.addWidget(edit_min)
        lay.addWidget(QLabel("Max:"))
        lay.addWidget(edit_max)
        if extra_after_range is not None:
            extra_label, extra_widget = extra_after_range
            lay.addWidget(extra_label)
            lay.addWidget(extra_widget)
        lay.addStretch(0)
        return row

    def _set_axis_range_edits(self, axis_name: str, edit_min: QLineEdit, edit_max: QLineEdit) -> None:
        meta = self.parent_app.global_bounds.get(axis_name)
        if not meta:
            edit_min.setText("")
            edit_max.setText("")
            return
        scale_int = bool(meta.get("is_int"))
        fmt = "{:.0f}" if scale_int else "{:.2f}"
        edit_min.setText(fmt.format(float(meta["min"])))
        edit_max.setText(fmt.format(float(meta["max"])))

    @staticmethod
    def _parse_edit_float(edit: QLineEdit) -> float | None:
        try:
            return float(edit.text()) if edit.text().strip() else None
        except Exception:
            return None

    @staticmethod
    def _plotly_axis_range(vmin: float | None, vmax: float | None, data_vals: list[float]) -> dict:
        """Build Plotly axis settings from user limits (may extend beyond plotted points)."""
        if vmin is None and vmax is None:
            return {}
        vals = data_vals or []
        lo = float(vmin) if vmin is not None else (min(vals) if vals else 0.0)
        hi = float(vmax) if vmax is not None else (max(vals) if vals else lo + 1.0)
        if lo > hi:
            lo, hi = hi, lo
        return {"range": [lo, hi], "autorange": False}

    def _numeric_column_names(self) -> list[str]:
        cols = list(self.parent_app.global_bounds.keys()) if getattr(self.parent_app, "global_bounds", None) else []
        if not cols and self.parent_app is not None:
            cols = [h for h in self.parent_app.headers[2:]]
        return cols

    @staticmethod
    def _populate_optional_axis_combo(combo: QComboBox, cols: list[str], default: str) -> None:
        PlotWidget._set_axis_combo_items(combo, cols, previous=default, allow_none=True)

    @staticmethod
    def _set_axis_combo_items(
        combo: QComboBox,
        cols: list[str],
        *,
        previous: str,
        allow_none: bool,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        if allow_none:
            combo.addItem(AXIS_NONE)
        combo.addItems(cols)
        if previous and combo.findText(previous) >= 0:
            combo.setCurrentText(previous)
        elif allow_none and (not previous or previous == AXIS_NONE or normalize_axis_name(previous) is None):
            combo.setCurrentIndex(0)
        elif cols:
            combo.setCurrentIndex(0 if not allow_none else 1)
        combo.blockSignals(False)

    def _combo_axis_name(self, combo: QComboBox) -> str | None:
        return normalize_axis_name(combo.currentText())

    def _current_plot_type(self) -> str:
        key = self.plot_type_combo.currentData()
        return key if isinstance(key, str) else PLOT_TYPE_SCATTER

    def _effective_plot_mode(self) -> str | None:
        return resolve_plot_mode(
            self._current_plot_type(),
            self.x_combo.currentText(),
            self.y_combo.currentText(),
            self.z_combo.currentText(),
        )

    def _infer_plot_mode(self) -> str | None:
        return infer_plot_mode(
            self.x_combo.currentText(),
            self.y_combo.currentText(),
            self.z_combo.currentText(),
        )

    def _is_single_column_plot(self) -> bool:
        ptype = self._current_plot_type()
        if ptype in (PLOT_TYPE_BOX, PLOT_TYPE_VIOLIN):
            return True
        return ptype == PLOT_TYPE_SCATTER and self._infer_plot_mode() == "Histogram"

    def _is_heatmap_plot(self) -> bool:
        return self._current_plot_type() == PLOT_TYPE_HEATMAP

    def _shows_x_bin_width(self) -> bool:
        ptype = self._current_plot_type()
        return ptype in (PLOT_TYPE_SCATTER, PLOT_TYPE_HEATMAP)

    def _shows_y_bin_width(self) -> bool:
        return self._is_heatmap_plot()

    def _supports_scatter_selection(self) -> bool:
        mode = self._effective_plot_mode()
        return mode in ("2D", "3D")

    def _plot_source_row_indices(self) -> list[int]:
        """Source-model rows for plotting (visible/filtered rows; optional selection-only)."""
        model = self.parent_app._table_model
        only_sel = None
        n_sel_now = len(self.parent_app._selected_logical_rows())
        if n_sel_now > 0 and self.only_selected_cb.isChecked():
            only_sel = list(self.parent_app._selected_logical_rows())
        visible = self.parent_app._visible_source_row_indices()
        return snapshot_scope_row_indices(
            model.rowCount(),
            only_selected_rows=only_sel,
            visible_row_indices=visible,
        )

    def _collect_points(self) -> tuple[list[float], list[float], list[float], list[int], str, str, str | None]:
        mode = self._effective_plot_mode()
        if mode not in ("2D", "3D", "Heatmap"):
            return [], [], [], [], "", "", None

        xname = self._combo_axis_name(self.x_combo) or ""
        yname = self._combo_axis_name(self.y_combo) or ""
        if not xname or not yname:
            return [], [], [], [], "", "", None

        h_map = {h: i for i, h in enumerate(self.parent_app.headers)}
        xi = h_map.get(xname)
        yi = h_map.get(yname)
        if xi is None or yi is None:
            return [], [], [], [], xname, yname, None

        n_sel_now = len(self.parent_app._selected_logical_rows()) if self.parent_app is not None else 0
        only_sel = n_sel_now > 0 and self.only_selected_cb.isChecked()
        allowed = self.parent_app._selected_oids_set() if only_sel else None
        if only_sel and not allowed:
            QMessageBox.warning(self, "Plot", "“Plot only selected rows” is checked but nothing is selected.")
            return [], [], [], [], xname, yname, None

        is3d = mode == "3D"
        zname = self._combo_axis_name(self.z_combo) if is3d else None
        zi = h_map.get(zname) if is3d else None
        if is3d and zi is None:
            return [], [], [], [], xname, yname, zname

        xmin = self._parse_edit_float(self.xmin)
        xmax = self._parse_edit_float(self.xmax)
        ymin = self._parse_edit_float(self.ymin)
        ymax = self._parse_edit_float(self.ymax)
        zmin = self._parse_edit_float(self.zmin) if is3d else None
        zmax = self._parse_edit_float(self.zmax) if is3d else None

        fx: list[float] = []
        fy: list[float] = []
        fz: list[float] = []
        foids: list[int] = []
        m = self.parent_app._table_model
        for r in self._plot_source_row_indices():
            oid = int(m.row_oid(r))
            if allowed is not None and oid not in allowed:
                continue
            xv = safe_float(m.cell_text(r, xi))
            yv = safe_float(m.cell_text(r, yi))
            if xv is None or yv is None:
                continue
            xv = float(xv)
            yv = float(yv)
            if xmin is not None and xv < xmin:
                continue
            if xmax is not None and xv > xmax:
                continue
            if ymin is not None and yv < ymin:
                continue
            if ymax is not None and yv > ymax:
                continue
            if is3d:
                zv = safe_float(m.cell_text(r, zi)) if zi is not None else None
                if zv is None:
                    continue
                zv = float(zv)
                if zmin is not None and zv < zmin:
                    continue
                if zmax is not None and zv > zmax:
                    continue
                fz.append(zv)
            fx.append(xv)
            fy.append(yv)
            foids.append(oid)
        return fx, fy, fz, foids, xname, yname, zname

    def _collect_histogram(self) -> tuple[list[float], list[int], str]:
        """Values and oids for a single-column histogram."""
        xname = self._combo_axis_name(self.x_combo) or ""
        if not xname:
            return [], [], ""
        h_map = {h: i for i, h in enumerate(self.parent_app.headers)}
        xi = h_map.get(xname)
        if xi is None:
            return [], [], xname

        n_sel_now = len(self.parent_app._selected_logical_rows()) if self.parent_app is not None else 0
        only_sel = n_sel_now > 0 and self.only_selected_cb.isChecked()
        allowed = self.parent_app._selected_oids_set() if only_sel else None
        if only_sel and not allowed:
            QMessageBox.warning(self, "Plot", "“Plot only selected rows” is checked but nothing is selected.")
            return [], [], xname

        xmin = self._parse_edit_float(self.xmin)
        xmax = self._parse_edit_float(self.xmax)
        vals: list[float] = []
        oids: list[int] = []
        m = self.parent_app._table_model
        for r in self._plot_source_row_indices():
            oid = int(m.row_oid(r))
            if allowed is not None and oid not in allowed:
                continue
            xv = safe_float(m.cell_text(r, xi))
            if xv is None:
                continue
            xv = float(xv)
            if xmin is not None and xv < xmin:
                continue
            if xmax is not None and xv > xmax:
                continue
            vals.append(xv)
            oids.append(oid)
        return vals, oids, xname

    def _load_plot_shell(self) -> None:
        from .plotly_shell import write_interactive_plot_shell

        write_interactive_plot_shell(self._plot_shell_path)
        self.web.load(QUrl.fromLocalFile(str(self._plot_shell_path)))

    def _annotate_scatter_selection_meta(self, fig: go.Figure) -> None:
        """Tell the Plotly shell which traces map to table row indices (skip fit lines, etc.)."""
        if not self._supports_scatter_selection():
            return
        indices: list[int] = []
        for i, tr in enumerate(fig.data):
            t = getattr(tr, "type", None)
            mode = str(getattr(tr, "mode", "") or "")
            if t in ("scatter", "scattergl") and "markers" in mode:
                indices.append(i)
            elif t == "scatter3d" and i == 0:
                indices.append(i)
        if indices:
            fig.update_layout(meta={"molmanager_selection_traces": indices})

    def _push_plotly_figure(self, fig: go.Figure) -> None:
        from .plotly_html import figure_payload_json

        self._annotate_scatter_selection_meta(fig)
        self._pending_payload_json = figure_payload_json(fig)
        self._last_browser_opened_path = None
        if self._web_ready:
            self._apply_pending_payload()
            QTimer.singleShot(0, self.sync_from_table_selection)

    def _apply_pending_payload(self) -> None:
        if not self._web_ready or not self._pending_payload_json:
            return
        js_arg = json.dumps(self._pending_payload_json)
        self.web.page().runJavaScript(f"window.molmanagerApply({js_arg});")
        self._arm_ignore_plot_clear()
        QTimer.singleShot(300, self.sync_from_table_selection)

    def sync_from_table_selection(self) -> None:
        """Highlight plot points for the current table row selection."""
        if not self._plotted_oids or self.parent_app is None:
            return
        if not self._supports_scatter_selection():
            return
        selected = self.parent_app._selected_oids_set()
        self._selected_point_indices = point_indices_for_oids(self._plotted_oids, selected)
        self._arm_ignore_plot_clear()
        self._sync_plot_selection_visual()

    def _sync_plot_selection_visual(self) -> None:
        if not self._web_ready:
            self._pending_table_selection_sync = True
            return
        self._pending_table_selection_sync = False
        idxs = sorted(self._selected_point_indices)
        js_arg = json.dumps(idxs)
        self.web.page().runJavaScript(f"window.molmanagerSetSelection({js_arg});")

    def _clear_plot_table_selection(self, *, update_plot: bool = True) -> None:
        self._selected_point_indices = set()
        self._ignore_plot_clear_until = 0.0
        clear_table_selection_from_plot(self.parent_app)
        if update_plot:
            self._sync_plot_selection_visual()

    def _arm_ignore_plot_clear(self, ms: int = 500) -> None:
        self._ignore_plot_clear_until = time.monotonic() + (ms / 1000.0)

    def _render_empty_plot(self, title: str) -> None:
        self._stats_panel.set_lines(["No statistics for the current plot."])
        fig = go.Figure()
        fig.update_layout(
            title=title,
            xaxis={"visible": False},
            yaxis={"visible": False},
            annotations=[{"text": title, "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5, "showarrow": False}],
            margin={"l": 20, "r": 20, "t": 50, "b": 20},
        )
        self._push_plotly_figure(fig)

    def _on_web_load_finished(self, ok: bool) -> None:
        if not ok:
            self._fallback_open_in_browser("Plot view failed to load in embedded renderer.")
            return

        def _after_probe(result) -> None:
            if not bool(result):
                self._fallback_open_in_browser("Embedded Plotly renderer is not supported on this system.")
                return
            self._web_ready = True
            self._apply_pending_payload()
            QTimer.singleShot(0, self.sync_from_table_selection)
            if self._pending_table_selection_sync:
                QTimer.singleShot(0, self.sync_from_table_selection)

        QTimer.singleShot(
            0,
            lambda: self.web.page().runJavaScript(
                "typeof window.Plotly !== 'undefined' && typeof window.molmanagerApply === 'function'",
                _after_probe,
            ),
        )

    def _fallback_open_in_browser(self, reason: str) -> None:
        path = str(self._plot_shell_path)
        if self._last_browser_opened_path == path:
            return
        self._last_browser_opened_path = path
        webbrowser.open(self._plot_shell_path.as_uri())
        self.parent_app.status_label.setText(f"Plot fallback: opened in browser ({reason})")

    def _empty_plot_hint(self) -> str:
        ptype = self._current_plot_type()
        if ptype == PLOT_TYPE_SCATTER:
            return "Choose X. Leave Y and Z as None for a histogram; set Y for 2D; set Y and Z for 3D."
        if ptype in (PLOT_TYPE_BOX, PLOT_TYPE_VIOLIN):
            return "Choose a numeric column for the X axis."
        if ptype == PLOT_TYPE_LINE_2D:
            return "Choose X and Y columns for the line chart."
        if ptype == PLOT_TYPE_HEATMAP:
            return "Choose X and Y columns for the heatmap."
        return "Choose axes for the current plot type."

    def plot(self):
        ptype = self._current_plot_type()
        if ptype in (PLOT_TYPE_BOX, PLOT_TYPE_VIOLIN):
            self._plot_distribution(ptype)
        elif ptype == PLOT_TYPE_LINE_2D:
            self._plot_line_2d()
        elif ptype == PLOT_TYPE_HEATMAP:
            self._plot_heatmap()
        else:
            mode = self._effective_plot_mode()
            if mode is None:
                self._render_empty_plot(self._empty_plot_hint())
            elif mode == "Histogram":
                self._plot_histogram()
            else:
                self._plot_scatter(mode)
        self._update_color_controls()

    def _plot_scatter(self, mode: str) -> None:
        self._hist_edges = []
        self._hist_vals = []
        self._hist_oids = []
        self._clear_heatmap_state()
        fx, fy, fz, foids, xname, yname, zname = self._collect_points()
        if not xname or not yname:
            self._render_empty_plot(self._empty_plot_hint())
            return
        is3d = mode == "3D"
        xmin = self._parse_edit_float(self.xmin)
        xmax = self._parse_edit_float(self.xmax)
        ymin = self._parse_edit_float(self.ymin)
        ymax = self._parse_edit_float(self.ymax)
        zmin = self._parse_edit_float(self.zmin) if is3d else None
        zmax = self._parse_edit_float(self.zmax) if is3d else None

        if not fx:
            self._plotted_oids = []
            self._selected_point_indices = set()
            fig = go.Figure()
            if is3d:
                fig.update_layout(
                    scene={
                        "xaxis": {"title": xname, **self._plotly_axis_range(xmin, xmax, [])},
                        "yaxis": {"title": yname, **self._plotly_axis_range(ymin, ymax, [])},
                        "zaxis": {"title": zname or "Z", **self._plotly_axis_range(zmin, zmax, [])},
                    },
                    margin={"l": 20, "r": 20, "t": 20, "b": 20},
                )
            else:
                fig.update_layout(
                    xaxis={"title": xname, **self._plotly_axis_range(xmin, xmax, [])},
                    yaxis={"title": yname, **self._plotly_axis_range(ymin, ymax, [])},
                    margin={"l": 50, "r": 20, "t": 20, "b": 45},
                )
            self._stats_panel.set_lines(["No statistics for the current plot."])
            self._push_plotly_figure(fig)
            self.parent_app.status_label.setText("Plot: no points for current axis/range/scope.")
            return

        self._plotted_oids = list(foids)
        self._selected_point_indices = {i for i in self._selected_point_indices if 0 <= i < len(self._plotted_oids)}
        selected_points = sorted(self._selected_point_indices) if self._selected_point_indices else []
        marker = self._scatter_marker_for_oids(foids, point_size=4 if is3d else 6)

        fig = go.Figure()
        if is3d:
            fig.add_trace(
                go.Scatter3d(
                    x=fx,
                    y=fy,
                    z=fz,
                    mode="markers",
                    marker=marker,
                    name="Points",
                )
            )
            if selected_points:
                sx = [fx[i] for i in selected_points]
                sy = [fy[i] for i in selected_points]
                sz = [fz[i] for i in selected_points]
                fig.add_trace(
                    go.Scatter3d(
                        x=sx,
                        y=sy,
                        z=sz,
                        mode="markers",
                        marker={"size": 7, "opacity": 1.0, "color": "#d62828"},
                        name="Selected",
                    )
                )
            fig.update_layout(
                scene={
                    "xaxis": {"title": xname, **self._plotly_axis_range(xmin, xmax, fx)},
                    "yaxis": {"title": yname, **self._plotly_axis_range(ymin, ymax, fy)},
                    "zaxis": {"title": zname or "Z", **self._plotly_axis_range(zmin, zmax, fz)},
                },
                margin={"l": 20, "r": 20, "t": 20, "b": 20},
            )
            stats_lines = (
                summarize_univariate(fx, label=xname)
                + summarize_univariate(fy, label=yname)
                + summarize_univariate(fz, label=zname or "Z")
            )
            self._present_analysis_dialog(stats_lines, None)
        else:
            fig.add_trace(
                go.Scatter(
                    x=fx,
                    y=fy,
                    mode="markers",
                    marker=marker,
                    selectedpoints=selected_points if selected_points else None,
                    selected={"marker": {"size": 9, "color": "#d62828", "opacity": 1.0}},
                    unselected={"marker": {"opacity": 0.35}},
                )
            )
            fig.update_layout(
                xaxis={"title": xname, **self._plotly_axis_range(xmin, xmax, fx)},
                yaxis={"title": yname, **self._plotly_axis_range(ymin, ymax, fy)},
                dragmode="lasso",
                margin={"l": 50, "r": 20, "t": 20, "b": 45},
            )
            fit_summary = self._add_xy_fit_trace(fig, fx, fy)
            self._present_analysis_dialog(
                summarize_xy(fx, fy, x_label=xname, y_label=yname),
                fit_summary,
            )

        self._push_plotly_figure(fig)
        self.parent_app.status_label.setText(f"Plot: rendered {len(fx):,} point(s).")

    def _plot_line_2d(self) -> None:
        self._clear_heatmap_state()
        fx, fy, _fz, foids, xname, yname, _zname = self._collect_points()
        if not xname or not yname:
            self._render_empty_plot(self._empty_plot_hint())
            return
        xmin = self._parse_edit_float(self.xmin)
        xmax = self._parse_edit_float(self.xmax)
        ymin = self._parse_edit_float(self.ymin)
        ymax = self._parse_edit_float(self.ymax)
        if not fx:
            self._plotted_oids = []
            self._selected_point_indices = set()
            fig = go.Figure()
            fig.update_layout(
                xaxis={"title": xname, **self._plotly_axis_range(xmin, xmax, [])},
                yaxis={"title": yname, **self._plotly_axis_range(ymin, ymax, [])},
                margin={"l": 50, "r": 20, "t": 20, "b": 45},
            )
            self._stats_panel.set_lines(["No statistics for the current plot."])
            self._push_plotly_figure(fig)
            self.parent_app.status_label.setText("Line plot: no points for current axis/range/scope.")
            return
        ordered = sorted(zip(fx, fy, foids), key=lambda t: t[0])
        fx, fy, foids = [list(c) for c in zip(*ordered)]
        self._plotted_oids = list(foids)
        self._selected_point_indices = {i for i in self._selected_point_indices if 0 <= i < len(self._plotted_oids)}
        selected_points = sorted(self._selected_point_indices) if self._selected_point_indices else []
        marker = self._scatter_marker_for_oids(foids, point_size=5)
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=fx,
                y=fy,
                mode="lines+markers",
                line={"color": "#2a74d6", "width": 1.5},
                marker=marker,
                selectedpoints=selected_points if selected_points else None,
                selected={"marker": {"size": 9, "color": "#d62828", "opacity": 1.0}},
                unselected={"marker": {"opacity": 0.35}},
            )
        )
        fig.update_layout(
            xaxis={"title": xname, **self._plotly_axis_range(xmin, xmax, fx)},
            yaxis={"title": yname, **self._plotly_axis_range(ymin, ymax, fy)},
            dragmode="lasso",
            margin={"l": 50, "r": 20, "t": 20, "b": 45},
        )
        fit_summary = self._add_xy_fit_trace(fig, fx, fy)
        self._present_analysis_dialog(
            summarize_xy(fx, fy, x_label=xname, y_label=yname),
            fit_summary,
        )
        self._push_plotly_figure(fig)
        self.parent_app.status_label.setText(f"Line plot: {len(fx):,} point(s).")

    def _plot_distribution(self, kind: str) -> None:
        self._clear_heatmap_state()
        vals, oids, xname = self._collect_histogram()
        if not xname:
            self._render_empty_plot(self._empty_plot_hint())
            return
        xmin = self._parse_edit_float(self.xmin)
        xmax = self._parse_edit_float(self.xmax)
        label = "Box plot" if kind == PLOT_TYPE_BOX else "Violin"
        if not vals:
            self._plotted_oids = []
            self._selected_point_indices = set()
            fig = go.Figure()
            fig.update_layout(
                yaxis={"title": xname, **self._plotly_axis_range(xmin, xmax, [])},
                margin={"l": 50, "r": 20, "t": 20, "b": 45},
            )
            self._stats_panel.set_lines(["No statistics for the current plot."])
            self._push_plotly_figure(fig)
            self.parent_app.status_label.setText(f"{label}: no values for current column/range/scope.")
            return
        self._plotted_oids = list(oids)
        self._selected_point_indices = set()
        trace_kwargs: dict = {
            "y": vals,
            "name": xname,
            "marker": {"color": "#2a74d6", "line": {"color": "#1d3557", "width": 1}},
        }
        if kind == PLOT_TYPE_BOX:
            trace_kwargs["boxmean"] = "sd"
            trace = go.Box(**trace_kwargs)
        else:
            trace = go.Violin(**trace_kwargs)
        fig = go.Figure(data=[trace])
        fig.update_layout(
            yaxis={"title": xname, **self._plotly_axis_range(xmin, xmax, vals)},
            showlegend=False,
            margin={"l": 50, "r": 20, "t": 20, "b": 45},
        )
        self._present_analysis_dialog(summarize_univariate(vals, label=xname), None)
        self._push_plotly_figure(fig)
        self.parent_app.status_label.setText(f"{label}: {len(vals):,} value(s) in {xname!r}.")

    def _plot_histogram(self) -> None:
        self._clear_heatmap_state()
        vals, oids, xname = self._collect_histogram()
        if not xname:
            self._render_empty_plot("Choose a column for the histogram.")
            return
        xmin = self._parse_edit_float(self.xmin)
        xmax = self._parse_edit_float(self.xmax)
        bin_width = self._parse_edit_float(self.hist_bin_width)
        if not vals:
            self._hist_edges = []
            self._hist_vals = []
            self._hist_oids = []
            self._plotted_oids = []
            self._selected_point_indices = set()
            fig = go.Figure()
            fig.update_layout(
                xaxis={"title": xname, **self._plotly_axis_range(xmin, xmax, [])},
                yaxis={"title": "Count"},
                margin={"l": 50, "r": 20, "t": 20, "b": 45},
            )
            self._stats_panel.set_lines(["No statistics for the current plot."])
            self._push_plotly_figure(fig)
            self.parent_app.status_label.setText("Histogram: no values for current column/range/scope.")
            return
        self._hist_vals = list(vals)
        self._hist_oids = list(oids)
        self._plotted_oids = list(oids)
        self._selected_point_indices = set()
        edges, width = compute_histogram_bin_edges(
            vals, bin_width=bin_width, xmin=xmin, xmax=xmax
        )
        self._hist_edges = edges
        hist_kwargs: dict = {
            "x": vals,
            "xbins": {"start": edges[0], "end": edges[-1], "size": width},
        }
        fig = go.Figure(
            data=[
                go.Histogram(
                    **hist_kwargs,
                    marker={"color": "#2a74d6", "line": {"color": "white", "width": 0.5}},
                )
            ]
        )
        fig.update_layout(
            xaxis={"title": xname, **self._plotly_axis_range(xmin, xmax, vals)},
            yaxis={"title": "Count"},
            bargap=0.02,
            margin={"l": 50, "r": 20, "t": 20, "b": 45},
        )
        fit_summary = self._add_histogram_fit_trace(fig, vals, edges, bin_width=width)
        self._present_analysis_dialog(summarize_univariate(vals, label=xname), fit_summary)
        self._push_plotly_figure(fig)
        self.parent_app.status_label.setText(f"Histogram: {len(vals):,} value(s) in {xname!r}.")

    def _clear_heatmap_state(self) -> None:
        self._heat_x = []
        self._heat_y = []
        self._heat_oids = []
        self._heat_x_edges = []
        self._heat_y_edges = []

    def _plot_heatmap(self) -> None:
        self._hist_edges = []
        self._hist_vals = []
        self._hist_oids = []
        self._clear_heatmap_state()
        fx, fy, _fz, foids, xname, yname, _zname = self._collect_points()
        if not xname or not yname:
            self._render_empty_plot(self._empty_plot_hint())
            return
        xmin = self._parse_edit_float(self.xmin)
        xmax = self._parse_edit_float(self.xmax)
        ymin = self._parse_edit_float(self.ymin)
        ymax = self._parse_edit_float(self.ymax)
        x_bin_width = self._parse_edit_float(self.hist_bin_width)
        y_bin_width = self._parse_edit_float(self.heatmap_y_bin_width)

        if not fx:
            self._plotted_oids = []
            self._selected_point_indices = set()
            fig = go.Figure()
            fig.update_layout(
                xaxis={"title": xname, **self._plotly_axis_range(xmin, xmax, [])},
                yaxis={"title": yname, **self._plotly_axis_range(ymin, ymax, [])},
                margin={"l": 50, "r": 20, "t": 20, "b": 45},
            )
            self._stats_panel.set_lines(["No statistics for the current plot."])
            self._push_plotly_figure(fig)
            self.parent_app.status_label.setText("Heatmap: no points for current axis/range/scope.")
            return

        x_edges, x_width = compute_histogram_bin_edges(
            fx, bin_width=x_bin_width, xmin=xmin, xmax=xmax
        )
        y_edges, y_width = compute_histogram_bin_edges(
            fy, bin_width=y_bin_width, xmin=ymin, xmax=ymax
        )
        colorscale = resolve_plot_colorscale(self.colorscale_combo.currentText())
        fig, counts = build_heatmap_figure(
            fx,
            fy,
            x_label=xname,
            y_label=yname,
            x_edges=x_edges,
            y_edges=y_edges,
            colorscale=colorscale,
        )
        self._heat_x = list(fx)
        self._heat_y = list(fy)
        self._heat_oids = list(foids)
        self._heat_x_edges = list(x_edges)
        self._heat_y_edges = list(y_edges)
        self._plotted_oids = list(foids)
        self._selected_point_indices = set()
        stats = summarize_heatmap(
            fx,
            fy,
            x_label=xname,
            y_label=yname,
            x_edges=x_edges,
            y_edges=y_edges,
            counts=counts,
        )
        self._present_analysis_dialog(stats, None)
        self._push_plotly_figure(fig)
        self.parent_app.status_label.setText(
            f"Heatmap: {len(fx):,} point(s), {len(x_edges) - 1}×{len(y_edges) - 1} bins."
        )

    def _on_heatmap_cell_clicked(self, x_value: float, y_value: float) -> None:
        oids = oids_in_heatmap_cell(
            self._heat_x,
            self._heat_y,
            self._heat_oids,
            self._heat_x_edges,
            self._heat_y_edges,
            x_value,
            y_value,
        )
        if not oids:
            return
        self._arm_ignore_plot_clear()
        self._select_rows_for_oids(oids)
        self.parent_app.status_label.setText(
            f"Heatmap: selected {len(oids):,} row(s) in the clicked cell."
        )

    def _on_histogram_points_selected(self, indices_json: str) -> None:
        try:
            raw = json.loads(indices_json)
        except json.JSONDecodeError:
            return
        if not isinstance(raw, list):
            return
        indices = [int(x) for x in raw if isinstance(x, (int, float)) and not isinstance(x, bool)]
        oids = oids_at_histogram_point_indices(self._hist_oids, indices)
        if not oids:
            return
        self._arm_ignore_plot_clear()
        self._select_rows_for_oids(oids)
        self.parent_app.status_label.setText(
            f"Histogram: selected {len(oids):,} row(s) in the clicked bar."
        )

    def _on_histogram_bin_clicked(self, bin_index: int) -> None:
        oids = oids_in_histogram_bin(self._hist_vals, self._hist_oids, self._hist_edges, bin_index)
        if not oids:
            return
        self._arm_ignore_plot_clear()
        self._select_rows_for_oids(oids)
        self.parent_app.status_label.setText(f"Histogram: selected {len(oids):,} row(s) in bin {bin_index + 1}.")

    def _select_rows_for_oids(self, oids: list[int]) -> None:
        source_rows: list[int] = []
        for oid in oids:
            row = self.parent_app.get_row_by_id(int(oid))
            if row >= 0:
                source_rows.append(int(row))
        if not source_rows:
            return
        self._select_rows_for_source_rows(source_rows)

    def _select_rows_for_point_indices(self, point_indices: list[int]) -> None:
        source_rows = source_rows_for_point_indices(self.parent_app, self._plotted_oids, point_indices)
        apply_table_selection_for_source_rows(self.parent_app, source_rows)

    def _select_rows_for_source_rows(self, source_rows: list[int]) -> None:
        apply_table_selection_for_source_rows(self.parent_app, source_rows)

    def _on_plot_point_clicked(self, point_index: int) -> None:
        if point_index < 0:
            return
        self._selected_point_indices = {int(point_index)}
        self._arm_ignore_plot_clear()
        self._select_rows_for_point_indices([int(point_index)])
        self._sync_plot_selection_visual()
        if 0 <= point_index < len(self._plotted_oids):
            oid = int(self._plotted_oids[point_index])
            row = self.parent_app.get_row_by_id(oid)
            if row >= 0:
                self.parent_app.status_label.setText(f"Plot: selected row {row + 1:,} (OID {oid}).")

    def _on_plot_points_selected(self, points_json: str) -> None:
        try:
            raw = json.loads(points_json or "[]")
            idxs = [int(x) for x in raw if isinstance(x, (int, float))]
        except Exception:
            idxs = []
        if not idxs:
            if time.monotonic() < self._ignore_plot_clear_until:
                return
            self._clear_plot_table_selection()
            self.parent_app.status_label.setText("Plot: selection cleared.")
            return
        self._selected_point_indices = {i for i in idxs if 0 <= i < len(self._plotted_oids)}
        if not self._selected_point_indices:
            return
        self._arm_ignore_plot_clear()
        sel_sorted = sorted(self._selected_point_indices)
        self._select_rows_for_point_indices(sel_sorted)
        self.parent_app.status_label.setText(f"Plot: selected {len(sel_sorted):,} point(s).")
        self._sync_plot_selection_visual()

    def _maybe_default_axis_range_edits(self, axis_key: str, axis_name: str, edit_min: QLineEdit, edit_max: QLineEdit) -> None:
        """Fill min/max from column bounds only when that axis column changes."""
        if self._prev_range_axis.get(axis_key) == axis_name:
            return
        self._prev_range_axis[axis_key] = axis_name
        self._set_axis_range_edits(axis_name, edit_min, edit_max)

    def _on_plot_type_change(self, _idx: int = 0) -> None:
        self._on_axis_change()

    def _on_axis_change(self):
        ptype = self._current_plot_type()
        if ptype == PLOT_TYPE_SCATTER:
            self._x_axis_row.setVisible(True)
            self._y_axis_row.setVisible(True)
            self._z_axis_row.setVisible(True)
            self.hist_bin_width_label.setText("Bin width:")
            self.hist_bin_width_label.setVisible(True)
            self.hist_bin_width.setVisible(True)
            self.heatmap_y_bin_width_label.setVisible(False)
            self.heatmap_y_bin_width.setVisible(False)
        elif ptype == PLOT_TYPE_HEATMAP:
            self._x_axis_row.setVisible(True)
            self._y_axis_row.setVisible(True)
            self._z_axis_row.setVisible(False)
            self.hist_bin_width_label.setText("X bin width:")
            self.hist_bin_width_label.setVisible(True)
            self.hist_bin_width.setVisible(True)
            self.heatmap_y_bin_width_label.setVisible(True)
            self.heatmap_y_bin_width.setVisible(True)
        else:
            if ptype == PLOT_TYPE_LINE_2D:
                mode = "2D"
            elif ptype in (PLOT_TYPE_BOX, PLOT_TYPE_VIOLIN):
                mode = "Histogram"
            else:
                mode = self._infer_plot_mode()
            is3d = mode == "3D"
            single_col = self._is_single_column_plot()
            self._y_axis_row.setVisible(not single_col)
            self._z_axis_row.setVisible(is3d)
            show_x_bw = self._shows_x_bin_width()
            self.hist_bin_width_label.setText("Bin width:")
            self.hist_bin_width_label.setVisible(show_x_bw)
            self.hist_bin_width.setVisible(show_x_bw)
            self.heatmap_y_bin_width_label.setVisible(False)
            self.heatmap_y_bin_width.setVisible(False)

        xname = self.x_combo.currentText()
        self._maybe_default_axis_range_edits("x", xname, self.xmin, self.xmax)
        yname = self._combo_axis_name(self.y_combo)
        if yname:
            self._maybe_default_axis_range_edits("y", yname, self.ymin, self.ymax)
        zname = self._combo_axis_name(self.z_combo)
        if zname:
            self._maybe_default_axis_range_edits("z", zname, self.zmin, self.zmax)
        self._update_color_controls()
        self._schedule_plot()

class PlotDialog(QDialog):
    """Floating window hosting a :class:`PlotWidget`."""

    def __init__(self, parent_app=None, plot_widget: PlotWidget | None = None):
        super().__init__(parent_app)
        self.parent_app = parent_app
        self.setWindowTitle("Plot Data")
        self.resize(980, 720)

        self._plot_widget = plot_widget if plot_widget is not None else PlotWidget(parent_app)
        self.only_selected_cb = self._plot_widget.only_selected_cb
        self._only_selected_scope_prefix = self._plot_widget._only_selected_scope_prefix

        root = QVBoxLayout(self)
        root.addWidget(self._plot_widget, 1)

        foot = QHBoxLayout()
        self._add_to_main_btn = QPushButton("Add to Main Window")
        self._add_to_main_btn.setAutoDefault(False)
        self._add_to_main_btn.setDefault(False)
        self._add_to_main_btn.setToolTip(
            "Dock this plot beside the table in the main window (like the filter panel)."
        )
        self._add_to_main_btn.clicked.connect(self._add_to_main_window)
        foot.addWidget(self._add_to_main_btn)
        foot.addStretch()
        root.addLayout(foot)

        self._configure_floating_plot_dialog()

    def _configure_floating_plot_dialog(self) -> None:
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._force_close = False
        for btn in self.findChildren(QPushButton):
            btn.setAutoDefault(False)
            btn.setDefault(False)

        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.WidgetWithChildrenShortcut)
        esc.activated.connect(self.close)
        make_window_minimizable(self)

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt API name
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            fw = self.focusWidget()
            if fw is not None and isinstance(fw, (QLineEdit, QComboBox, QAbstractSpinBox)):
                event.accept()
                return
        super().keyPressEvent(event)

    def _add_to_main_window(self) -> None:
        if self.parent_app is None or self._plot_widget is None:
            return
        teardown = getattr(self, "_scope_sync_disconnect", None)
        if callable(teardown):
            teardown()
        if not self.parent_app.dock_plot_widget(self._plot_widget):
            return
        self._plot_widget = None
        self._force_close = True
        self.close()

    def closeEvent(self, event):
        if self._force_close:
            self._force_close = False
        event.accept()
