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

"""Modeless MMP pair neighborhood network viewer."""

from __future__ import annotations

import logging
from typing import Any

from PyQt5.QtCore import QObject, Qt, QRunnable, QThreadPool, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..mmp_analysis import MmpPair, pairs_involving_oid
from ..mmp_neighborhood_analysis import MmpNetworkGraph, build_mmp_network_graph
from .mmp_neighborhood_plot import build_mmp_neighborhood_figure
from .plotly_interactive_view import PlotlyInteractiveView
from .qt_widget_utils import make_window_minimizable

logger = logging.getLogger(__name__)

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: F401

    _HAS_WEB = True
except Exception:
    _HAS_WEB = False


class _LayoutSignals(QObject):
    finished = pyqtSignal(int, object)  # generation, MmpNetworkGraph
    failed = pyqtSignal(int, str)


class _LayoutWorker(QRunnable):
    """Build network layout off the GUI thread."""

    def __init__(
        self,
        generation: int,
        pairs: list[MmpPair],
        *,
        focus_oids: list[int] | None,
        max_hops: int,
        signals: _LayoutSignals,
    ):
        super().__init__()
        self.setAutoDelete(True)
        self._generation = int(generation)
        self._pairs = pairs
        self._focus_oids = focus_oids
        self._max_hops = int(max_hops)
        self._signals = signals

    def run(self) -> None:
        try:
            graph = build_mmp_network_graph(
                self._pairs,
                focus_oids=self._focus_oids,
                max_hops=self._max_hops if self._focus_oids else 0,
            )
            self._signals.finished.emit(self._generation, graph)
        except Exception as exc:
            logger.exception("MMP network layout failed")
            self._signals.failed.emit(self._generation, str(exc) or "Layout failed.")


class MmpNeighborhoodMapDialog(QDialog):
    """Interactive MMP pair network; click a node to select it in the table."""

    def __init__(
        self,
        parent: Any,
        pairs: list[MmpPair],
        *,
        activity_column: str,
    ):
        super().__init__(parent)
        self._app = parent
        self._pairs: list[MmpPair] = []
        self._graph: MmpNetworkGraph | None = None
        self._activity_column = activity_column
        self._current_oid: int | None = None
        self._layout_generation = 0
        self._layout_signals = _LayoutSignals(self)
        self._layout_signals.finished.connect(self._on_layout_finished)
        self._layout_signals.failed.connect(self._on_layout_failed)
        self._pending_focus: list[int] | None = None

        self.setWindowTitle("MMP Pair Network")
        self.resize(980, 700)
        self.setMinimumSize(640, 480)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)

        root = QVBoxLayout(self)
        self._meta = QLabel()
        self._meta.setWordWrap(True)
        root.addWidget(self._meta)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Neighborhood hops:"))
        self._hops_sb = QSpinBox()
        self._hops_sb.setRange(0, 8)
        self._hops_sb.setValue(0)
        self._hops_sb.setToolTip(
            "0 = full network. When > 0, show only nodes within N hops of the "
            "current table selection (or the last clicked node)."
        )
        controls.addWidget(self._hops_sb)
        self._btn_rebuild = QPushButton("Rebuild focus")
        self._btn_rebuild.setToolTip(
            "Rebuild the graph using the current table selection as focus seeds "
            "(uses Neighborhood hops)."
        )
        controls.addWidget(self._btn_rebuild)
        controls.addStretch()
        root.addLayout(controls)

        self._plot_view: PlotlyInteractiveView | None = None
        if _HAS_WEB and parent is not None:
            self._plot_view = _NetworkPlotView(parent, self)
            self._plot_view.pointActivated.connect(self._on_point_activated)
            root.addWidget(self._plot_view, 1)
        else:
            missing = QLabel("Plotly WebEngine view is unavailable in this build.")
            missing.setAlignment(Qt.AlignCenter)
            root.addWidget(missing, 1)

        self._detail = QLabel("Click a node to select that molecule.")
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self._detail)

        actions = QHBoxLayout()
        self._btn_browse = QPushButton("Browse pairs for node")
        self._btn_browse.setEnabled(False)
        self._btn_browse.setToolTip(
            "Open the MMP pair browser for pairs involving the selected node."
        )
        self._btn_select = QPushButton("Select node in table")
        self._btn_select.setEnabled(False)
        self._btn_clear_sel = QPushButton("Clear Selection")
        self._btn_clear_sel.setToolTip("Clear the plot and table selection.")
        actions.addWidget(self._btn_browse)
        actions.addWidget(self._btn_select)
        actions.addWidget(self._btn_clear_sel)
        actions.addStretch()
        root.addLayout(actions)

        self._btn_rebuild.clicked.connect(self._rebuild_from_table_selection)
        self._btn_browse.clicked.connect(self._browse_current)
        self._btn_select.clicked.connect(self._select_current)
        self._btn_clear_sel.clicked.connect(self._clear_selection)

        make_window_minimizable(self)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.set_pairs(pairs, activity_column=activity_column)

    def set_pairs(self, pairs: list[MmpPair], *, activity_column: str | None = None) -> None:
        self._pairs = list(pairs or [])
        if activity_column is not None:
            self._activity_column = activity_column
        self._current_oid = None
        self._btn_browse.setEnabled(False)
        self._btn_select.setEnabled(False)
        self._rebuild_graph(focus_oids=None)

    def _focus_oids_from_table(self) -> list[int]:
        app = self._app
        if app is None:
            return []
        try:
            return sorted(int(o) for o in app._selected_oids_set())
        except Exception:
            return []

    def _rebuild_from_table_selection(self) -> None:
        hops = int(self._hops_sb.value())
        seeds = self._focus_oids_from_table()
        if hops > 0 and not seeds and self._current_oid is not None:
            seeds = [int(self._current_oid)]
        self._rebuild_graph(focus_oids=seeds if hops > 0 else None)

    def _set_busy(self, busy: bool) -> None:
        self._btn_rebuild.setEnabled(not busy)
        self._hops_sb.setEnabled(not busy)

    def _rebuild_graph(self, *, focus_oids: list[int] | None) -> None:
        hops = int(self._hops_sb.value())
        self._pending_focus = list(focus_oids) if focus_oids else None
        self._layout_generation += 1
        generation = self._layout_generation
        n_pairs = len(self._pairs)
        self._meta.setText(
            f"Laying out network ({n_pairs} pair(s))…  ·  "
            f"node color = {self._activity_column}"
        )
        self._detail.setText("Computing layout in the background…")
        self._set_busy(True)
        worker = _LayoutWorker(
            generation,
            self._pairs,
            focus_oids=self._pending_focus,
            max_hops=hops,
            signals=self._layout_signals,
        )
        QThreadPool.globalInstance().start(worker)

    def _on_layout_finished(self, generation: int, graph: object) -> None:
        if int(generation) != self._layout_generation:
            return
        self._set_busy(False)
        if not isinstance(graph, MmpNetworkGraph):
            self._meta.setText("Layout failed.")
            return
        self._graph = graph
        focus_oids = self._pending_focus
        hops = int(self._hops_sb.value())
        n_nodes = len(self._graph.node_oids)
        n_edges = len(self._graph.edges)
        focus_txt = ""
        if focus_oids and hops > 0:
            focus_txt = f"  ·  focus {len(focus_oids)} seed(s), {hops} hop(s)"
        self._meta.setText(
            f"{n_nodes} molecule(s), {n_edges} MMP edge(s)  ·  "
            f"node color = {self._activity_column}, size = degree  ·  "
            f"edge color = signed Δ{focus_txt}"
        )
        self._detail.setText("Click a node to select that molecule.")
        if self._plot_view is None:
            return
        try:
            fig = build_mmp_neighborhood_figure(
                self._graph, activity_column=self._activity_column
            )
            self._plot_view.push_figure(fig, list(self._graph.node_oids))
        except Exception:
            logger.exception("Failed to render MMP neighborhood figure")
            self._meta.setText(
                f"{n_nodes} molecule(s), {n_edges} MMP edge(s)  ·  plot render failed"
            )

    def _on_layout_failed(self, generation: int, message: str) -> None:
        if int(generation) != self._layout_generation:
            return
        self._set_busy(False)
        self._meta.setText(message or "Layout failed.")
        self._detail.setText("Try reducing scope or using Neighborhood hops with a table selection.")

    def _on_point_activated(self, point_index: int) -> None:
        if self._graph is None:
            return
        if not (0 <= point_index < len(self._graph.node_oids)):
            self._current_oid = None
            self._btn_browse.setEnabled(False)
            self._btn_select.setEnabled(False)
            return
        oid = int(self._graph.node_oids[point_index])
        self._current_oid = oid
        deg = int(self._graph.degrees.get(oid, 0))
        act = self._graph.activities.get(oid)
        act_txt = f"{act:.4g}" if act is not None else "—"
        self._detail.setText(
            f"ID {oid}  ·  degree={deg}  ·  {self._activity_column}={act_txt}"
        )
        self._btn_browse.setEnabled(True)
        self._btn_select.setEnabled(True)
        self._select_current()

    def _select_current(self) -> None:
        app = self._app
        if app is None or self._current_oid is None:
            return
        try:
            app.select_table_oids([self._current_oid], extra_status="MMP network")
        except Exception:
            pass

    def _clear_selection(self) -> None:
        self._current_oid = None
        self._btn_browse.setEnabled(False)
        self._btn_select.setEnabled(False)
        self._detail.setText("Click a node to select that molecule.")
        if self._plot_view is not None:
            try:
                self._plot_view.clear_table_selection(update_plot=True)
            except Exception:
                pass
        elif self._app is not None:
            try:
                self._app.clear_table_selection()
            except Exception:
                pass

    def _browse_current(self) -> None:
        app = self._app
        if app is None or self._current_oid is None:
            return
        subset = pairs_involving_oid(self._pairs, self._current_oid)
        if not subset:
            return
        try:
            app._open_mmp_browser(subset, activity_column=self._activity_column)
        except Exception:
            pass


class _NetworkPlotView(PlotlyInteractiveView):
    """Plotly view that notifies when a network node is activated."""

    pointActivated = pyqtSignal(int)

    def _on_plot_point_clicked(self, point_index: int, *, additive: bool = False) -> None:
        super()._on_plot_point_clicked(point_index, additive=additive)
        self.pointActivated.emit(int(point_index))

    def _on_plot_points_selected(self, points_json: str, additive: bool = False) -> None:
        super()._on_plot_points_selected(points_json, additive=additive)
        if self._selected_point_indices:
            self.pointActivated.emit(int(sorted(self._selected_point_indices)[0]))
