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

"""Tests for multi-pane workspace layout presets and docking into panes."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtWidgets import QApplication, QLabel, QWidget

from molmanager.ui.main_window.workspace_layout import (
    DEFAULT_LAYOUT_ID,
    LAYOUT_QUADRANTS,
    LAYOUT_TABLE_ONLY,
    LAYOUT_TABLE_SIDE,
    LAYOUT_TABLE_SINGLE,
    LAYOUT_TABLE_STACK,
    WorkspaceLayoutManager,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _manager(qapp) -> WorkspaceLayoutManager:
    table = QWidget()
    return WorkspaceLayoutManager(table)


def test_default_layout_is_table_stack(qapp):
    mgr = _manager(qapp)
    assert mgr.layout_id == DEFAULT_LAYOUT_ID == LAYOUT_TABLE_STACK
    assert len(mgr.plot_panes()) == 2
    assert all(p.is_empty() for p in mgr.plot_panes())


def test_apply_layout_pane_counts(qapp):
    mgr = _manager(qapp)
    mgr.apply_layout(LAYOUT_TABLE_ONLY, preserve_plots=False)
    assert len(mgr.plot_panes()) == 0
    mgr.apply_layout(LAYOUT_TABLE_SINGLE, preserve_plots=False)
    assert len(mgr.plot_panes()) == 1
    mgr.apply_layout(LAYOUT_TABLE_SIDE, preserve_plots=False)
    assert len(mgr.plot_panes()) == 2
    mgr.apply_layout(LAYOUT_QUADRANTS, preserve_plots=False)
    assert len(mgr.plot_panes()) == 3
    mgr.apply_layout(LAYOUT_TABLE_STACK, preserve_plots=False)
    assert len(mgr.plot_panes()) == 2


def test_table_only_releases_all_plots(qapp):
    mgr = _manager(qapp)
    w0 = QLabel("a")
    w1 = QLabel("b")
    mgr.dock_into_pane(mgr.plot_panes()[0], w0)
    mgr.dock_into_pane(mgr.plot_panes()[1], w1)
    extras = mgr.apply_layout(LAYOUT_TABLE_ONLY, preserve_plots=True)
    assert mgr.layout_id == LAYOUT_TABLE_ONLY
    assert mgr.plot_panes() == []
    assert set(extras) == {w0, w1}
    assert mgr.preferred_pane() is None


def test_dock_into_pane_and_release(qapp):
    mgr = _manager(qapp)
    pane = mgr.plot_panes()[0]
    widget = QLabel("plot")
    prev = mgr.dock_into_pane(pane, widget)
    assert prev is None
    assert pane.plot_widget() is widget
    assert not pane.is_empty()
    assert mgr.pane_for_widget(widget) is pane
    assert list(mgr.iter_docked_widgets()) == [widget]
    assert mgr.release_widget(widget) is True
    assert pane.is_empty()


def test_apply_layout_preserves_plots_and_returns_extras(qapp):
    mgr = _manager(qapp)
    w0 = QLabel("a")
    w1 = QLabel("b")
    w2 = QLabel("c")
    mgr.dock_into_pane(mgr.plot_panes()[0], w0)
    mgr.dock_into_pane(mgr.plot_panes()[1], w1)
    # Stack has 2 panes; add a third logically by switching to single after 3 docks on stack
    # First expand to side (2), then we only have 2. Use stack with 2, switch to single → 1 extra.
    extras = mgr.apply_layout(LAYOUT_TABLE_SINGLE, preserve_plots=True)
    assert len(mgr.plot_panes()) == 1
    assert mgr.plot_panes()[0].plot_widget() is w0
    assert extras == [w1]
    # Re-dock leftover and another, then go to single again from stack with 2
    mgr.apply_layout(LAYOUT_TABLE_STACK, preserve_plots=True)
    assert len(mgr.plot_panes()) == 2
    mgr.dock_into_pane(mgr.plot_panes()[0], w0)
    mgr.dock_into_pane(mgr.plot_panes()[1], w1)
    # Can't fit w2 on stack; simulate three kept by manually calling with three
    # via temporary side layout then adding third through apply from a custom keep list:
    mgr.dock_into_pane(mgr.plot_panes()[0], w2)
    # Now panes hold w2 and w1; apply single keeps first only
    extras2 = mgr.apply_layout(LAYOUT_TABLE_SINGLE, preserve_plots=True)
    assert mgr.plot_panes()[0].plot_widget() is w2
    assert w1 in extras2


def test_splitter_size_roundtrip(qapp):
    mgr = _manager(qapp)
    payload = mgr.collect_splitter_sizes()
    assert payload["layout_id"] == LAYOUT_TABLE_STACK
    assert "sizes" in payload
    mgr.apply_layout(LAYOUT_TABLE_SIDE, preserve_plots=False)
    mgr.restore_splitter_sizes(payload)  # different layout; sizes keys may not match count
    payload2 = mgr.collect_splitter_sizes()
    mgr.restore_splitter_sizes(payload2)
    assert mgr.collect_splitter_sizes()["layout_id"] == LAYOUT_TABLE_SIDE


def test_preferred_pane_tracks_activation(qapp):
    mgr = _manager(qapp)
    p0, p1 = mgr.plot_panes()
    mgr.set_preferred_pane(p1)
    assert mgr.preferred_pane() is p1
    mgr.dock_into_pane(p0, QLabel("x"))
    assert mgr.preferred_pane() is p0
