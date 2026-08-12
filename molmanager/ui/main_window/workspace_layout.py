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

"""Multi-pane workspace layouts: fixed-left table + configurable plot panes."""

from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..dockable_plot import PLOT_PANEL_BASE_MINIMUM_WIDTH, PLOT_PANEL_DEFAULT_WIDTH

LAYOUT_TABLE_ONLY = "table_only"
LAYOUT_TABLE_SINGLE = "table_single"
LAYOUT_TABLE_STACK = "table_stack"
LAYOUT_TABLE_SIDE = "table_side"
LAYOUT_QUADRANTS = "quadrants"
DEFAULT_LAYOUT_ID = LAYOUT_TABLE_STACK

LAYOUT_PRESETS: tuple[tuple[str, str], ...] = (
    (LAYOUT_TABLE_ONLY, "Table Only"),
    (LAYOUT_TABLE_SINGLE, "Table | 1 plot"),
    (LAYOUT_TABLE_STACK, "Table | 2 stacked plots"),
    (LAYOUT_TABLE_SIDE, "Table | 2 side-by-side plots"),
    (LAYOUT_QUADRANTS, "Quadrants (table upper-left)"),
)


class _PaneActivateFilter(QObject):
    """Forward mouse presses on a docked plot to activate its host pane."""

    def __init__(self, pane: "PlotPane"):
        super().__init__(pane)
        self._pane = pane

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — Qt API
        if event.type() == QEvent.MouseButtonPress:
            self._pane.activated.emit(self._pane)
        return False


class PlotPane(QFrame):
    """Host for one docked plot widget, or an empty placeholder."""

    activated = pyqtSignal(object)  # PlotPane

    def __init__(self, pane_id: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.pane_id = pane_id
        self._plot_widget: QWidget | None = None
        self._activate_filter = _PaneActivateFilter(self)
        self.setObjectName("PlotPane")
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumWidth(PLOT_PANEL_BASE_MINIMUM_WIDTH // 2)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(2, 2, 2, 2)
        self._root.setSpacing(0)
        self._placeholder = QLabel("Plot pane\n(Add to Main Window…)")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet("color: palette(mid); padding: 12px;")
        self._root.addWidget(self._placeholder, 1)
        self._set_active_style(False)

    def plot_widget(self) -> QWidget | None:
        return self._plot_widget

    def is_empty(self) -> bool:
        return self._plot_widget is None

    def display_title(self) -> str:
        w = self._plot_widget
        if w is None:
            return "empty"
        title = getattr(w, "_window_title", None)
        if isinstance(title, str) and title.strip():
            return title.strip()
        name = w.__class__.__name__
        if name.endswith("PlotPanel"):
            return name[: -len("PlotPanel")] or name
        if name.endswith("Widget"):
            return name[: -len("Widget")] or name
        return name

    def set_plot_widget(self, widget: QWidget | None) -> QWidget | None:
        """Place ``widget`` in this pane; return the previous occupant (detached)."""
        previous = self._plot_widget
        if previous is not None:
            self._uninstall_activate_filter(previous)
            self._root.removeWidget(previous)
            previous.setParent(None)
            self._plot_widget = None
        if widget is not None:
            self._placeholder.hide()
            self._root.addWidget(widget, 1)
            self._plot_widget = widget
            self._install_activate_filter(widget)
        else:
            self._placeholder.show()
        return previous

    def _install_activate_filter(self, widget: QWidget) -> None:
        widget.installEventFilter(self._activate_filter)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self._activate_filter)

    def _uninstall_activate_filter(self, widget: QWidget) -> None:
        try:
            widget.removeEventFilter(self._activate_filter)
        except RuntimeError:
            pass
        try:
            for child in widget.findChildren(QWidget):
                try:
                    child.removeEventFilter(self._activate_filter)
                except RuntimeError:
                    pass
        except RuntimeError:
            pass

    def clear_plot_widget(self) -> QWidget | None:
        return self.set_plot_widget(None)

    def set_active(self, active: bool) -> None:
        self._set_active_style(active)

    def _set_active_style(self, active: bool) -> None:
        if active:
            self.setStyleSheet(
                "QFrame#PlotPane { border: 2px solid palette(highlight); border-radius: 2px; }"
            )
        else:
            self.setStyleSheet(
                "QFrame#PlotPane { border: 1px solid palette(mid); border-radius: 2px; }"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt API
        self.activated.emit(self)
        super().mousePressEvent(event)


class WorkspaceLayoutManager(QWidget):
    """Owns the content splitter tree: table region + plot panes."""

    layout_changed = pyqtSignal(str)

    def __init__(self, table_area: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self._table_area = table_area
        self._layout_id = DEFAULT_LAYOUT_ID
        self._panes: list[PlotPane] = []
        self._preferred_pane_id: str | None = None
        self._splitters: list[QSplitter] = []
        self._root_ly = QVBoxLayout(self)
        self._root_ly.setContentsMargins(0, 0, 0, 0)
        self._root_ly.setSpacing(0)
        self._workspace_root: QWidget | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.apply_layout(DEFAULT_LAYOUT_ID, preserve_plots=False)

    @property
    def layout_id(self) -> str:
        return self._layout_id

    def plot_panes(self) -> list[PlotPane]:
        return list(self._panes)

    def preferred_pane(self) -> PlotPane | None:
        if not self._panes:
            return None
        if self._preferred_pane_id:
            for p in self._panes:
                if p.pane_id == self._preferred_pane_id:
                    return p
        for p in self._panes:
            if p.is_empty():
                return p
        return self._panes[0]

    def set_preferred_pane(self, pane: PlotPane | None) -> None:
        self._preferred_pane_id = pane.pane_id if pane is not None else None
        for p in self._panes:
            p.set_active(p is pane)

    def pane_for_widget(self, widget: QWidget | None) -> PlotPane | None:
        if widget is None:
            return None
        for p in self._panes:
            if p.plot_widget() is widget:
                return p
        return None

    def iter_docked_widgets(self):
        for p in self._panes:
            w = p.plot_widget()
            if w is not None:
                yield w

    def find_pane(self, pane_id: str) -> PlotPane | None:
        for p in self._panes:
            if p.pane_id == pane_id:
                return p
        return None

    def dock_into_pane(self, pane: PlotPane, widget: QWidget) -> QWidget | None:
        """Dock ``widget`` into ``pane``. Returns previous occupant if any."""
        # Detach from any other pane first.
        other = self.pane_for_widget(widget)
        if other is not None and other is not pane:
            other.clear_plot_widget()
        previous = pane.set_plot_widget(widget)
        self.set_preferred_pane(pane)
        return previous

    def release_widget(self, widget: QWidget) -> bool:
        pane = self.pane_for_widget(widget)
        if pane is None:
            return False
        pane.clear_plot_widget()
        return True

    def collect_splitter_sizes(self) -> dict:
        """Serializable nested splitter sizes for the current layout."""
        sizes: dict[str, list[int]] = {}
        for i, sp in enumerate(self._splitters):
            try:
                sizes[f"splitter_{i}"] = [int(s) for s in sp.sizes()]
            except RuntimeError:
                pass
        return {"layout_id": self._layout_id, "sizes": sizes}

    def restore_splitter_sizes(self, payload: dict | None) -> None:
        if not isinstance(payload, dict):
            return
        sizes_map = payload.get("sizes")
        if not isinstance(sizes_map, dict):
            return
        for i, sp in enumerate(self._splitters):
            key = f"splitter_{i}"
            vals = sizes_map.get(key)
            if isinstance(vals, list) and len(vals) == sp.count():
                try:
                    sp.setSizes([max(0, int(v)) for v in vals])
                except (RuntimeError, TypeError, ValueError):
                    pass

    def apply_layout(
        self,
        layout_id: str,
        *,
        preserve_plots: bool = True,
        on_extra_plot: Callable[[QWidget], None] | None = None,
    ) -> list[QWidget]:
        """
        Rebuild the workspace for ``layout_id``.

        Returns plot widgets that no longer fit (caller should undock them).
        """
        if layout_id not in {p[0] for p in LAYOUT_PRESETS}:
            layout_id = DEFAULT_LAYOUT_ID

        kept: list[QWidget] = []
        if preserve_plots:
            kept = [w for w in self.iter_docked_widgets()]

        # Detach table and plots before destroying the tree.
        self._table_area.setParent(None)
        for w in kept:
            w.setParent(None)
        for p in self._panes:
            p.set_plot_widget(None)

        if self._workspace_root is not None:
            self._root_ly.removeWidget(self._workspace_root)
            self._workspace_root.setParent(None)
            self._workspace_root.deleteLater()
            self._workspace_root = None

        self._splitters.clear()
        self._panes.clear()
        self._layout_id = layout_id

        if layout_id == LAYOUT_TABLE_ONLY:
            root = self._build_table_only()
        elif layout_id == LAYOUT_QUADRANTS:
            root = self._build_quadrants()
        elif layout_id == LAYOUT_TABLE_SIDE:
            root = self._build_table_with_plot_area(Qt.Horizontal, 2)
        elif layout_id == LAYOUT_TABLE_SINGLE:
            root = self._build_table_with_plot_area(None, 1)
        else:
            root = self._build_table_with_plot_area(Qt.Vertical, 2)

        self._workspace_root = root
        self._root_ly.addWidget(root, 1)

        extras: list[QWidget] = []
        for i, widget in enumerate(kept):
            if i < len(self._panes):
                self._panes[i].set_plot_widget(widget)
                sync = getattr(widget, "_sync_footer_chrome", None)
                if callable(sync):
                    sync()
            else:
                extras.append(widget)
                if on_extra_plot is not None:
                    on_extra_plot(widget)

        pref = self.preferred_pane()
        self.set_preferred_pane(pref)
        for p in self._panes:
            p.activated.connect(self._on_pane_activated)

        self.layout_changed.emit(self._layout_id)
        return extras

    def _on_pane_activated(self, pane: PlotPane) -> None:
        self.set_preferred_pane(pane)

    def _new_pane(self, index: int) -> PlotPane:
        pane = PlotPane(f"pane_{index}", self)
        self._panes.append(pane)
        return pane

    def _track_splitter(self, splitter: QSplitter) -> QSplitter:
        self._splitters.append(splitter)
        return splitter

    def _build_table_only(self) -> QWidget:
        """Full-width table with no plot panes."""
        host = QWidget()
        ly = QVBoxLayout(host)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(0)
        ly.addWidget(self._table_area, 1)
        return host

    def _build_table_with_plot_area(
        self, plot_orientation: Qt.Orientation | None, n_panes: int
    ) -> QWidget:
        outer = self._track_splitter(QSplitter(Qt.Horizontal))
        outer.setHandleWidth(6)
        outer.setChildrenCollapsible(False)
        outer.addWidget(self._table_area)
        if n_panes <= 1 or plot_orientation is None:
            pane = self._new_pane(0)
            outer.addWidget(pane)
            outer.setStretchFactor(0, 1)
            outer.setStretchFactor(1, 1)
            outer.setSizes([700, PLOT_PANEL_DEFAULT_WIDTH])
            return outer

        plot_split = self._track_splitter(QSplitter(plot_orientation))
        plot_split.setHandleWidth(6)
        plot_split.setChildrenCollapsible(False)
        for i in range(n_panes):
            plot_split.addWidget(self._new_pane(i))
            plot_split.setStretchFactor(i, 1)
        if plot_orientation == Qt.Vertical:
            plot_split.setSizes([400, 400])
        else:
            plot_split.setSizes([420, 420])
        outer.addWidget(plot_split)
        outer.setStretchFactor(0, 1)
        outer.setStretchFactor(1, 1)
        outer.setSizes([700, PLOT_PANEL_DEFAULT_WIDTH])
        return outer

    def _build_quadrants(self) -> QWidget:
        """Table upper-left; three plot panes in UR, LL, LR."""
        outer = self._track_splitter(QSplitter(Qt.Vertical))
        outer.setHandleWidth(6)
        outer.setChildrenCollapsible(False)

        top = self._track_splitter(QSplitter(Qt.Horizontal))
        top.setHandleWidth(6)
        top.setChildrenCollapsible(False)
        top.addWidget(self._table_area)
        top.addWidget(self._new_pane(0))
        top.setStretchFactor(0, 1)
        top.setStretchFactor(1, 1)
        top.setSizes([700, 500])

        bottom = self._track_splitter(QSplitter(Qt.Horizontal))
        bottom.setHandleWidth(6)
        bottom.setChildrenCollapsible(False)
        bottom.addWidget(self._new_pane(1))
        bottom.addWidget(self._new_pane(2))
        bottom.setStretchFactor(0, 1)
        bottom.setStretchFactor(1, 1)
        bottom.setSizes([600, 600])

        outer.addWidget(top)
        outer.addWidget(bottom)
        outer.setStretchFactor(0, 1)
        outer.setStretchFactor(1, 1)
        outer.setSizes([450, 450])
        return outer
