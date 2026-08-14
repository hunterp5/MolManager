# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

"""Modeless SALI map: fingerprint similarity vs |Δactivity|."""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..sali_analysis import SaliPoint
from .plotly_interactive_view import PlotlyInteractiveView
from .qt_widget_utils import make_window_minimizable
from .result_plot_panel import DockableResultPlotPanel
from .sali_plot import build_sali_figure

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: F401

    _HAS_WEB = True
except Exception:
    _HAS_WEB = False


class SaliMapPanel(DockableResultPlotPanel):
    """Interactive SALI scatter; click a point to select both molecules."""

    def __init__(
        self,
        parent_app: Any,
        points: list[SaliPoint] | None = None,
        *,
        activity_column: str = "",
        fp_choice: str = "",
        metric: str = "Tanimoto",
        parent=None,
    ):
        super().__init__(
            parent_app,
            window_title="SALI",
            floating_dialog_cls=SaliMapDialog,
            default_color_hint="SALI index",
            parent=parent,
        )
        self._points: list[SaliPoint] = []
        self._activity_column = activity_column or ""
        self._fp_choice = fp_choice or ""
        self._metric = metric or "Tanimoto"
        self._current_index: int | None = None

        self._meta = QLabel()
        self._meta.setWordWrap(True)
        self._root.addWidget(self._meta)

        self._plot_view: PlotlyInteractiveView | None = None
        if _HAS_WEB and parent_app is not None:
            self._plot_view = _SaliPlotView(parent_app, self)
            self._plot_view.pointActivated.connect(self._on_point_activated)
            self._root.addWidget(self._plot_view, 1)
        else:
            missing = QLabel("Plotly WebEngine view is unavailable in this build.")
            missing.setAlignment(Qt.AlignCenter)
            self._root.addWidget(missing, 1)

        self._detail = QLabel("Click a point to select the pair.")
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._root.addWidget(self._detail)

        actions = QHBoxLayout()
        self._btn_browse = QPushButton("Browse pair")
        self._btn_browse.setEnabled(False)
        self._btn_browse.setToolTip(
            "Open the SALI pair browser for the selected point (step through plot pairs)."
        )
        self._btn_select = QPushButton("Select pair in table")
        self._btn_select.setEnabled(False)
        actions.addWidget(self._btn_browse)
        actions.addWidget(self._btn_select)
        actions.addStretch()
        self._root.addLayout(actions)
        self._btn_browse.clicked.connect(self._browse_current)
        self._btn_select.clicked.connect(self._select_current)

        self._finish_layout()
        self.set_points(
            points or [],
            activity_column=activity_column,
            fp_choice=self._fp_choice,
            metric=self._metric,
        )

    def set_points(
        self,
        points: list[SaliPoint],
        *,
        activity_column: str | None = None,
        fp_choice: str | None = None,
        metric: str | None = None,
    ) -> None:
        self._points = list(points or [])
        if activity_column is not None:
            self._activity_column = activity_column
        if fp_choice is not None:
            self._fp_choice = fp_choice
        if metric is not None:
            self._metric = metric
        self._current_index = None
        self._btn_browse.setEnabled(False)
        self._btn_select.setEnabled(False)
        fp_txt = self._fp_choice or "fingerprint"
        self._meta.setText(
            f"{len(self._points)} pair(s)  ·  {fp_txt} / {self._metric}  ·  "
            f"default color = SALI = |Δ| / (1 − similarity)  ·  Δ relative to {self._activity_column}"
        )
        self._detail.setText("Click a point to select the pair.")
        self._reload_color_columns()
        self._rebuild_figure()

    def _encoding_sample_values(self):
        color_col = self.color_combo.currentText()
        if color_col == "(none)" or not self._points:
            return None
        pairs = [(p.oid_a, p.oid_b) for p in self._points[:64]]
        return self._column_values_for_oid_pairs(pairs, color_col)

    def _rebuild_figure(self) -> None:
        if self._plot_view is None:
            return
        sim_label = f"{self._metric} similarity"
        if self._fp_choice:
            sim_label = f"{self._fp_choice} ({self._metric})"
        pairs = [(p.oid_a, p.oid_b) for p in self._points]
        enc = self._resolved_encoding(oid_pairs=pairs)
        fig = build_sali_figure(
            self._points,
            activity_column=self._activity_column,
            similarity_label=sim_label,
            **enc,
        )
        oids = [p.oid_a for p in self._points]
        partners = [p.oid_b for p in self._points]
        self._plot_view.push_figure(fig, oids, partner_oids=partners)
        self._update_spectrum_controls()

    def _on_point_activated(self, point_index: int) -> None:
        if not (0 <= point_index < len(self._points)):
            self._current_index = None
            self._btn_browse.setEnabled(False)
            self._btn_select.setEnabled(False)
            return
        self._current_index = int(point_index)
        point = self._points[self._current_index]
        sign = "+" if point.signed_delta >= 0 else ""
        self._detail.setText(
            f"IDs {point.oid_a} ↔ {point.oid_b}  ·  "
            f"similarity={point.similarity:.3f}  ·  "
            f"Δ{self._activity_column} = {sign}{point.signed_delta:.4g}  ·  "
            f"SALI={point.sali:.4g}"
        )
        self._btn_browse.setEnabled(True)
        self._btn_select.setEnabled(True)
        # Table selection is applied by the plot view (both pair partners).

    def _select_current(self) -> None:
        if self._current_index is None or self.parent_app is None:
            return
        if not (0 <= self._current_index < len(self._points)):
            return
        point = self._points[self._current_index]
        try:
            self.parent_app.select_table_oids(
                [point.oid_a, point.oid_b], extra_status="SALI"
            )
        except Exception:
            pass

    def _browse_current(self) -> None:
        if self._current_index is None or self.parent_app is None:
            return
        if not (0 <= self._current_index < len(self._points)):
            return
        open_browser = getattr(self.parent_app, "_open_sali_browser", None)
        if not callable(open_browser):
            return
        try:
            open_browser(
                self._points,
                activity_column=self._activity_column,
                fp_choice=self._fp_choice,
                metric=self._metric,
                start_index=self._current_index,
            )
        except Exception:
            pass

    def _clear_selection(self) -> None:
        self._current_index = None
        self._btn_browse.setEnabled(False)
        self._btn_select.setEnabled(False)
        self._detail.setText("Click a point to select the pair.")
        if self._plot_view is not None:
            try:
                self._plot_view.clear_table_selection(update_plot=True)
            except Exception:
                pass
        elif self.parent_app is not None:
            try:
                self.parent_app.clear_table_selection()
            except Exception:
                pass


class SaliMapDialog(QDialog):
    """Floating window hosting a :class:`SaliMapPanel`."""

    def __init__(
        self,
        parent: Any,
        points: list[SaliPoint] | None = None,
        *,
        activity_column: str = "",
        fp_choice: str = "",
        metric: str = "Tanimoto",
        panel: SaliMapPanel | None = None,
    ):
        super().__init__(parent)
        self.parent_app = parent
        if panel is not None:
            self._panel = panel
            self._panel.setParent(self)
            self._panel.parent_app = parent
        else:
            self._panel = SaliMapPanel(
                parent,
                points or [],
                activity_column=activity_column,
                fp_choice=fp_choice,
                metric=metric,
            )

        self.setWindowTitle("SALI")
        self.resize(960, 680)
        self.setMinimumSize(640, 480)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._force_close = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._panel, 1)
        self._panel._sync_footer_chrome()
        make_window_minimizable(self)

    def set_points(self, *args, **kwargs) -> None:
        if self._panel is not None:
            self._panel.set_points(*args, **kwargs)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API name
        if self._force_close:
            self._force_close = False
        event.accept()


class _SaliPlotView(PlotlyInteractiveView):
    """Plotly view that notifies when a SALI point is activated."""

    pointActivated = pyqtSignal(int)

    def _on_plot_point_clicked(self, point_index: int, *, additive: bool = False) -> None:
        super()._on_plot_point_clicked(point_index, additive=additive)
        self.pointActivated.emit(int(point_index))

    def _on_plot_points_selected(self, points_json: str, additive: bool = False) -> None:
        super()._on_plot_points_selected(points_json, additive=additive)
        if self._selected_point_indices:
            self.pointActivated.emit(int(sorted(self._selected_point_indices)[0]))
