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

"""plot_table_sync helpers."""

from __future__ import annotations

from molmanager.ui.plot_table_sync import (
    apply_table_selection_for_source_rows,
    point_indices_for_oids,
    selected_oids_for_plot,
)


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


def test_apply_table_selection_skips_identical_selection() -> None:
    """Re-applying the same plot selection must not scroll / re-select (snap-back loop)."""
    from unittest.mock import MagicMock

    sm = MagicMock()
    row0 = MagicMock()
    row0.row.return_value = 0
    row2 = MagicMock()
    row2.row.return_value = 2
    sm.selectedRows.return_value = [row0, row2]

    table = MagicMock()
    table.selectionModel.return_value = sm
    view_model = MagicMock()
    view_model.columnCount.return_value = 3
    table.model.return_value = view_model

    app = MagicMock()
    app.table = table
    app._source_rows_to_view_rows.return_value = [0, 2]
    app._in_programmatic_table_selection = False

    apply_table_selection_for_source_rows(app, [0, 2], scroll=True)

    sm.select.assert_not_called()
    table.scrollTo.assert_not_called()
