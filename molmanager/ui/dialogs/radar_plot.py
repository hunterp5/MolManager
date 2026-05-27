"""Radar (spider) plot tool — up to six numeric spokes (Data menu)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...medchem_space import snapshot_scope_row_indices
from ..plotly_html import finalize_plot_legend
from ...plot_radar import (
    MAX_RADAR_DISPLAY_ENTRIES,
    MAX_RADAR_TRACES,
    MAX_RADAR_VARIABLES,
    MIN_RADAR_VARIABLES,
    SPOKE_NONE,
    build_radar_figure,
    collect_radar_rows,
    compute_radar_normalization_bounds,
    filter_radar_rows_by_oids,
    resolve_entry_row_oid,
)
from ..plotly_interactive_view import PlotlyInteractiveView
from ..qt_widget_utils import make_window_minimizable
from .scope import selection_scope_checked

if TYPE_CHECKING:
    from ..main_window import ChemicalTableApp

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: F401

    _HAS_WEB = True
except ImportError:
    _HAS_WEB = False


class RadarPlotPanel(QWidget):
    """Six spoke dropdowns, optional row-ID entry fields, and Plotly radar chart."""

    def __init__(self, parent_app: ChemicalTableApp | None):
        super().__init__(None)
        self.parent_app = parent_app
        self._radar_oids: list[int] = []
        self._plot_debounce = QTimer(self)
        self._plot_debounce.setSingleShot(True)
        self._plot_debounce.timeout.connect(self.plot)

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
            self._plot_view.setMinimumHeight(300)
            self._plot_view._on_radar_trace_clicked = self._on_radar_trace_clicked  # type: ignore[attr-defined]
            plot_ly.addWidget(self._plot_view, 1)
            self._plot_placeholder = None
        else:
            self._plot_view = None
            self._plot_placeholder = QLabel(
                "Install PyQtWebEngine to show the interactive radar plot in this window."
            )
            self._plot_placeholder.setWordWrap(True)
            self._plot_placeholder.setAlignment(Qt.AlignCenter)
            plot_ly.addWidget(self._plot_placeholder, 1)
        root.addWidget(plot_host, 1)

        opts = QVBoxLayout()
        opts.setSpacing(6)

        scope = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh plot")
        self.refresh_btn.clicked.connect(self.plot)
        scope.addWidget(self.refresh_btn)
        scope.addStretch(1)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({n_sel} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        self.only_selected_cb.stateChanged.connect(self._schedule_plot)
        scope.addWidget(self.only_selected_cb)
        opts.addLayout(scope)

        spokes_gb = QGroupBox("Spokes (numeric columns)")
        spokes_ly = QVBoxLayout(spokes_gb)
        spoke_row = QHBoxLayout()
        spoke_row.setSpacing(6)
        self.spoke_combos: list[QComboBox] = []
        for i in range(MAX_RADAR_VARIABLES):
            spoke_row.addWidget(QLabel(f"{i + 1}:"))
            combo = QComboBox()
            combo.setToolTip(f"Spoke {i + 1}: choose a numeric column, or {SPOKE_NONE}.")
            combo.currentIndexChanged.connect(lambda _idx, n=i: self._on_spoke_changed(n))
            self.spoke_combos.append(combo)
            spoke_row.addWidget(combo, 1)
        spokes_ly.addLayout(spoke_row)

        self.entry_edits: list[QLineEdit] = []
        for row_start in (0, 3):
            entries_row = QHBoxLayout()
            entries_row.setSpacing(6)
            for i in range(row_start, min(row_start + 3, MAX_RADAR_DISPLAY_ENTRIES)):
                entries_row.addWidget(QLabel(f"Entry {i + 1}:"))
                edit = QLineEdit()
                edit.setPlaceholderText("Row ID")
                edit.setToolTip(
                    "OID or 1-based table row number. Leave empty to plot all rows in scope."
                )
                edit.textChanged.connect(self._schedule_plot)
                self.entry_edits.append(edit)
                entries_row.addWidget(edit, 1)
            spokes_ly.addLayout(entries_row)

        hint = QLabel(
            f"Choose {MIN_RADAR_VARIABLES}–{MAX_RADAR_VARIABLES} different spoke columns. "
            f"Type up to {MAX_RADAR_DISPLAY_ENTRIES} row IDs to plot only those entries; "
            f"leave entry fields empty to plot every row in scope (up to {MAX_RADAR_TRACES:,}). "
            "Spoke values are min–max normalized across all rows in scope before plotting. "
            "Click a trace to select that row in the table."
        )
        hint.setWordWrap(True)
        spokes_ly.addWidget(hint)
        opts.addWidget(spokes_gb)

        root.addLayout(opts)

        self.refresh_spoke_columns()
        if parent_app is not None:
            model = parent_app._table_model
            model.rowsRemoved.connect(self._schedule_plot)
            model.rowsInserted.connect(self._schedule_plot)
            model.modelReset.connect(self.refresh_spoke_columns)
            model.columnsInserted.connect(self.refresh_spoke_columns)
            model.columnsRemoved.connect(self.refresh_spoke_columns)
            model.headerDataChanged.connect(self._on_header_data_changed)
        self._schedule_plot()

    def _on_header_data_changed(self, orientation, first: int, last: int) -> None:  # noqa: ARG002
        if int(orientation) == Qt.Horizontal:
            self.refresh_spoke_columns()

    def _numeric_column_names(self) -> list[str]:
        if self.parent_app is None:
            return []
        cols = list(self.parent_app.global_bounds.keys()) if getattr(self.parent_app, "global_bounds", None) else []
        if not cols:
            cols = [h for h in self.parent_app.headers[2:] if h and h != "ID_HIDDEN"]
        return cols

    def refresh_spoke_columns(self) -> None:
        """Sync spoke dropdowns when table columns change."""
        if self.parent_app is None:
            return
        cols = self._numeric_column_names()
        previous = [c.currentText() for c in self.spoke_combos]
        for combo, prev in zip(self.spoke_combos, previous):
            combo.blockSignals(True)
            try:
                combo.clear()
                combo.addItem(SPOKE_NONE)
                for col in cols:
                    combo.addItem(col)
                idx = combo.findText(prev)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
            finally:
                combo.blockSignals(False)
        self._schedule_plot()

    def _selected_spoke_columns(self) -> list[str]:
        cols: list[str] = []
        for combo in self.spoke_combos:
            text = combo.currentText()
            if text and text != SPOKE_NONE:
                cols.append(text)
        return cols

    def _on_spoke_changed(self, spoke_index: int) -> None:
        combo = self.spoke_combos[spoke_index]
        chosen = combo.currentText()
        if chosen and chosen != SPOKE_NONE:
            for i, other in enumerate(self.spoke_combos):
                if i != spoke_index and other.currentText() == chosen:
                    combo.blockSignals(True)
                    try:
                        combo.setCurrentIndex(0)
                    finally:
                        combo.blockSignals(False)
                    QMessageBox.information(
                        self,
                        "Radar Plot",
                        f'Column "{chosen}" is already assigned to another spoke.',
                    )
                    return
        self._schedule_plot()

    def _selected_entry_oids(self) -> tuple[list[int] | None, list[str]]:
        """Return (oids to plot, or None for all rows) and invalid ID strings entered."""
        if self.parent_app is None:
            return None, []
        model = self.parent_app._table_model
        oids: list[int] = []
        invalid: list[str] = []
        for edit in self.entry_edits:
            text = edit.text().strip()
            if not text:
                continue
            oid = resolve_entry_row_oid(
                text,
                model=model,
                row_for_oid=self.parent_app.get_row_by_id,
            )
            if oid is None:
                invalid.append(text)
                continue
            if oid not in oids:
                oids.append(oid)
        return (oids or None), invalid

    def _schedule_plot(self) -> None:
        self._plot_debounce.start(80)

    def _scope_row_indices(self) -> list[int]:
        assert self.parent_app is not None
        model = self.parent_app._table_model
        only_sel = None
        if selection_scope_checked(self):
            only_sel = list(self.parent_app._selected_logical_rows())
        visible = self.parent_app._visible_source_row_indices()
        return snapshot_scope_row_indices(
            model.rowCount(),
            only_selected_rows=only_sel,
            visible_row_indices=visible,
        )

    def _scope_allowed_oids(self) -> set[int] | None:
        if not selection_scope_checked(self):
            return None
        return self.parent_app._selected_oids_set()

    def plot(self) -> None:
        columns = self._selected_spoke_columns()
        if len(columns) < MIN_RADAR_VARIABLES:
            self._radar_oids = []
            if self._plot_view is not None:
                self._plot_view.push_figure(_empty_radar_figure_hint(), [])
            return
        if self.parent_app is None:
            return

        oids, raw_rows = collect_radar_rows(
            self.parent_app._table_model,
            list(self.parent_app.headers),
            columns,
            allowed_oids=self._scope_allowed_oids(),
            row_indices=self._scope_row_indices(),
        )
        rows = [[float(v) for v in row] for row in raw_rows]
        if not rows:
            self._radar_oids = []
            if self._plot_view is not None:
                self._plot_view.push_figure(_empty_radar_figure_hint(), [])
            self.parent_app.status_label.setText(
                "Radar Plot: no rows with numeric values in all selected spokes."
            )
            return

        norm_mins, norm_maxs = compute_radar_normalization_bounds(rows)

        display_oids, invalid_ids = self._selected_entry_oids()
        if invalid_ids:
            self.parent_app.status_label.setText(
                "Radar Plot: unknown row ID(s): " + ", ".join(invalid_ids)
            )
        total_in_scope = len(oids)
        if display_oids is not None:
            oids, rows = filter_radar_rows_by_oids(oids, rows, display_oids)
            if not rows:
                self._radar_oids = []
                if self._plot_view is not None:
                    self._plot_view.push_figure(_empty_radar_figure_hint(), [])
                if not invalid_ids:
                    self.parent_app.status_label.setText(
                        "Radar Plot: entered row IDs have no data for the current spokes."
                    )
                return
        elif total_in_scope > MAX_RADAR_TRACES:
            oids = oids[:MAX_RADAR_TRACES]
            rows = rows[:MAX_RADAR_TRACES]

        self._radar_oids = list(oids)
        fig = build_radar_figure(
            columns,
            oids,
            rows,
            norm_mins=norm_mins,
            norm_maxs=norm_maxs,
        )
        if self._plot_view is not None:
            self._plot_view.push_figure(fig, oids)
        msg = f"Radar Plot: {len(rows):,} row(s), {len(columns)} spoke(s) (normalized)."
        if display_oids is None and total_in_scope > len(rows):
            msg = f"Radar Plot: showing first {MAX_RADAR_TRACES:,} of {total_in_scope:,} rows (normalized)."
        if not invalid_ids:
            self.parent_app.status_label.setText(msg)

    def _on_radar_trace_clicked(self, trace_index: int) -> None:
        if trace_index < 0 or trace_index >= len(self._radar_oids):
            return
        if self._plot_view is None or self.parent_app is None:
            return
        oid = int(self._radar_oids[trace_index])
        self._plot_view.select_oids([oid])
        row = self.parent_app.get_row_by_id(oid)
        if row >= 0:
            self.parent_app.status_label.setText(
                f"Radar Plot: selected row {row + 1:,} (OID {oid})."
            )


def _empty_radar_figure_hint():
    from plotly import graph_objects as go

    fig = go.Figure()
    fig.update_layout(
        polar={"radialaxis": {"visible": False}, "angularaxis": {"visible": False}},
        annotations=[
            {
                "text": f"Select {MIN_RADAR_VARIABLES}–{MAX_RADAR_VARIABLES} numeric spokes",
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": 14},
            }
        ],
        margin={"l": 40, "r": 40, "t": 30, "b": 30},
        showlegend=False,
    )
    return finalize_plot_legend(fig)


class RadarPlotDialog(QDialog):
    """Floating window for :class:`RadarPlotPanel`."""

    def __init__(self, parent: ChemicalTableApp | None, panel: RadarPlotPanel | None = None):
        super().__init__(parent)
        self.parent_app = parent
        if panel is not None:
            self._panel = panel
            self._panel.setParent(self)
            self._panel.parent_app = parent
        else:
            self._panel = RadarPlotPanel(parent)
        self.only_selected_cb = self._panel.only_selected_cb
        self._only_selected_scope_prefix = self._panel._only_selected_scope_prefix

        self.setWindowTitle("Radar Plot")
        self.resize(920, 720)

        root = QVBoxLayout(self)
        root.addWidget(self._panel, 1)

        foot = QHBoxLayout()
        foot.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        foot.addWidget(close_btn)
        root.addLayout(foot)

        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        make_window_minimizable(self)
