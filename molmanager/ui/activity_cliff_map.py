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

"""Modeless Activity Cliff Map: scatter of MMP change size vs |Δactivity|."""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..activity_cliff_analysis import ActivityCliffPoint, build_activity_cliff_points
from ..mmp_analysis import MmpPair
from .activity_cliff_plot import build_activity_cliff_figure
from .plotly_interactive_view import PlotlyInteractiveView
from .qt_widget_utils import make_window_minimizable

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: F401

    _HAS_WEB = True
except Exception:
    _HAS_WEB = False


class ActivityCliffMapDialog(QDialog):
    """Interactive cliff scatter; click a point to select the pair and browse evidence."""

    def __init__(
        self,
        parent: Any,
        pairs: list[MmpPair],
        *,
        activity_column: str,
        x_mode: str = "heavy_atoms",
    ):
        super().__init__(parent)
        self._app = parent
        self._pairs: list[MmpPair] = []
        self._points: list[ActivityCliffPoint] = []
        self._activity_column = activity_column
        self._x_mode = x_mode or "heavy_atoms"
        self._current_index: int | None = None

        self.setWindowTitle("Activity Cliff Map")
        self.resize(960, 680)
        self.setMinimumSize(640, 480)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

        root = QVBoxLayout(self)
        self._meta = QLabel()
        self._meta.setWordWrap(True)
        root.addWidget(self._meta)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("X axis:"))
        self._x_combo = QComboBox()
        self._x_combo.addItem("Changing heavy atoms", "heavy_atoms")
        self._x_combo.addItem("Fragment distance (1 − Tanimoto)", "frag_distance")
        idx = self._x_combo.findData(self._x_mode)
        if idx >= 0:
            self._x_combo.setCurrentIndex(idx)
        controls.addWidget(self._x_combo)
        controls.addStretch()
        root.addLayout(controls)

        self._plot_view: PlotlyInteractiveView | None = None
        if _HAS_WEB and parent is not None:
            self._plot_view = _CliffPlotView(parent, self)
            self._plot_view.pointActivated.connect(self._on_point_activated)
            root.addWidget(self._plot_view, 1)
        else:
            missing = QLabel("Plotly WebEngine view is unavailable in this build.")
            missing.setAlignment(Qt.AlignCenter)
            root.addWidget(missing, 1)

        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self._detail)

        actions = QHBoxLayout()
        self._btn_browse = QPushButton("Browse pair")
        self._btn_browse.setEnabled(False)
        self._btn_browse.setToolTip("Open the MMP pair browser for the selected cliff point")
        self._btn_select = QPushButton("Select pair in table")
        self._btn_select.setEnabled(False)
        actions.addWidget(self._btn_browse)
        actions.addWidget(self._btn_select)
        actions.addStretch()
        root.addLayout(actions)

        self._x_combo.currentIndexChanged.connect(lambda _i: self._rebuild_figure())
        self._btn_browse.clicked.connect(self._browse_current)
        self._btn_select.clicked.connect(self._select_current)

        make_window_minimizable(self)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.set_pairs(pairs, activity_column=activity_column, x_mode=self._x_mode)

    def set_pairs(
        self,
        pairs: list[MmpPair],
        *,
        activity_column: str | None = None,
        x_mode: str | None = None,
    ) -> None:
        self._pairs = list(pairs or [])
        if activity_column is not None:
            self._activity_column = activity_column
        if x_mode is not None:
            self._x_mode = x_mode
            idx = self._x_combo.findData(self._x_mode)
            if idx >= 0:
                self._x_combo.blockSignals(True)
                self._x_combo.setCurrentIndex(idx)
                self._x_combo.blockSignals(False)
        self._points = build_activity_cliff_points(self._pairs)
        self._current_index = None
        self._meta.setText(
            f"{len(self._points)} matched pair(s)  ·  Δ relative to {self._activity_column}  ·  "
            "color = signed Δ (red/blue)"
        )
        self._detail.setText("Click a point to inspect the cliff pair.")
        self._btn_browse.setEnabled(False)
        self._btn_select.setEnabled(False)
        self._rebuild_figure()

    def _rebuild_figure(self) -> None:
        if self._plot_view is None:
            return
        self._x_mode = str(self._x_combo.currentData() or "heavy_atoms")
        fig = build_activity_cliff_figure(
            self._points,
            activity_column=self._activity_column,
            x_mode=self._x_mode,
        )
        # Hover cards use the first OID; pair identity lives in customdata / parallel lists.
        oids = [p.oid_a for p in self._points]
        self._plot_view.push_figure(fig, oids)

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
            f"Δ{self._activity_column} = {sign}{point.signed_delta:.4g}  ·  "
            f"change HA={point.change_heavy_atoms}  ·  "
            f"frag distance={point.frag_distance:.3f}\n"
            f"{point.transform}"
        )
        self._btn_browse.setEnabled(True)
        self._btn_select.setEnabled(True)
        self._select_current()

    def _current_pair(self) -> MmpPair | None:
        if self._current_index is None:
            return None
        if not (0 <= self._current_index < len(self._points)):
            return None
        pair_index = self._points[self._current_index].pair_index
        if not (0 <= pair_index < len(self._pairs)):
            return None
        return self._pairs[pair_index]

    def _select_current(self) -> None:
        pair = self._current_pair()
        app = self._app
        if pair is None or app is None:
            return
        try:
            app.select_table_oids([pair.oid_a, pair.oid_b], extra_status="Activity cliff")
        except Exception:
            pass

    def _browse_current(self) -> None:
        pair = self._current_pair()
        app = self._app
        if pair is None or app is None:
            return
        try:
            app._open_mmp_browser([pair], activity_column=self._activity_column)
        except Exception:
            pass


class _CliffPlotView(PlotlyInteractiveView):
    """Plotly view that notifies the cliff dialog when a point is activated."""

    pointActivated = pyqtSignal(int)

    def _on_plot_point_clicked(self, point_index: int, *, additive: bool = False) -> None:
        super()._on_plot_point_clicked(point_index, additive=additive)
        self.pointActivated.emit(int(point_index))

    def _on_plot_points_selected(self, points_json: str, additive: bool = False) -> None:
        super()._on_plot_points_selected(points_json, additive=additive)
        if self._selected_point_indices:
            self.pointActivated.emit(int(sorted(self._selected_point_indices)[0]))
