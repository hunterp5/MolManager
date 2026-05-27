"""plot_table_sync helpers."""

from __future__ import annotations

from molmanager.ui.plot_table_sync import point_indices_for_oids


def test_point_indices_for_oids() -> None:
    plotted = [10, 20, 30]
    assert point_indices_for_oids(plotted, {20, 99}) == {1}
    assert point_indices_for_oids(plotted, frozenset()) == set()
    assert point_indices_for_oids([], {10}) == set()
