"""plot_table_sync helpers."""

from __future__ import annotations

from molmanager.ui.plot_table_sync import point_indices_for_oids, selected_oids_for_plot


def test_point_indices_for_oids() -> None:
    plotted = [10, 20, 30]
    assert point_indices_for_oids(plotted, {20, 99}) == {1}
    assert point_indices_for_oids(plotted, frozenset()) == set()
    assert point_indices_for_oids([], {10}) == set()


class _FakeModel:
    def __init__(self, oids_by_row: dict[int, int], highlighted: frozenset[int] | None = None) -> None:
        self._oids_by_row = oids_by_row
        self._highlighted = highlighted

    def row_oid(self, row: int) -> int:
        return self._oids_by_row[row]

    def highlighted_oids(self) -> frozenset[int] | None:
        return self._highlighted


class _FakeApp:
    def __init__(self) -> None:
        self._selected_oids_override = None
        self._logical_rows: list[int] = []
        self._table_model = _FakeModel({0: 10, 1: 20, 2: 30})

    def _selected_logical_rows(self) -> list[int]:
        return list(self._logical_rows)


def test_selected_oids_for_plot_qt_selection() -> None:
    app = _FakeApp()
    app._logical_rows = [0, 2]
    assert selected_oids_for_plot(app) == {10, 30}


def test_selected_oids_for_plot_override() -> None:
    app = _FakeApp()
    app._selected_oids_override = frozenset({99})
    app._logical_rows = [0]
    assert selected_oids_for_plot(app) == {99}


def test_selected_oids_for_plot_highlighted_fallback() -> None:
    app = _FakeApp()
    app._table_model = _FakeModel({0: 10, 1: 20}, highlighted=frozenset({20}))
    assert selected_oids_for_plot(app) == {20}
