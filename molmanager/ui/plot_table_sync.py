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

"""Shared table ↔ Plotly selection helpers (Plotter and PlotlyInteractiveView)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtCore import QItemSelectionModel
from PyQt5.QtWidgets import QAbstractItemView

from .table_selection import item_selection_for_view_rows

if TYPE_CHECKING:
    from .main_window import ChemicalTableApp


def selected_oids_for_plot(parent_app: ChemicalTableApp) -> set[int]:
    """
    OIDs that should drive plot highlighting — matches what the table shows as selected.

    Includes Qt selection, large-selection override, and model-level highlighted OIDs.
    """
    override = getattr(parent_app, "_selected_oids_override", None)
    if override:
        return {int(x) for x in override}
    oids: set[int] = set()
    model = parent_app._table_model
    for r in parent_app._selected_logical_rows():
        try:
            oids.add(int(model.row_oid(r)))
        except (IndexError, ValueError, TypeError):
            continue
    highlighted = model.highlighted_oids()
    if highlighted:
        oids |= {int(x) for x in highlighted}
    return oids


def point_indices_for_oids(plotted_oids: list[int], selected_oids: set[int] | frozenset[int]) -> set[int]:
    """Map table OIDs to scatter point indices for the current plot."""
    if not plotted_oids or not selected_oids:
        return set()
    sel = selected_oids if isinstance(selected_oids, frozenset) else frozenset(selected_oids)
    return {i for i, oid in enumerate(plotted_oids) if int(oid) in sel}


def source_rows_for_point_indices(parent_app: ChemicalTableApp, plotted_oids: list[int], point_indices: list[int]) -> list[int]:
    rows: list[int] = []
    for idx in point_indices:
        if idx < 0 or idx >= len(plotted_oids):
            continue
        row = parent_app.get_row_by_id(int(plotted_oids[idx]))
        if row >= 0:
            rows.append(int(row))
    return sorted(set(rows))


def apply_table_selection_for_source_rows(
    parent_app: ChemicalTableApp,
    source_rows: list[int],
    *,
    scroll: bool = True,
) -> None:
    """Select visible proxy rows for source-model row indices (plot lasso / click).

    When ``scroll`` is True (user-driven plot select), reveal the first selected row.
    Re-sync paths should pass ``scroll=False`` so table scrolling is not fought.
    """
    if not source_rows:
        return
    sm = parent_app.table.selectionModel()
    if sm is None:
        return
    view_model = parent_app.table.model()
    if view_model is None:
        return
    view_rows = parent_app._source_rows_to_view_rows(sorted(set(int(r) for r in source_rows)))
    if not view_rows:
        return
    # Plot re-echo / replot sync often re-applies the same selection; never fight user scroll.
    current_view = {ix.row() for ix in sm.selectedRows()}
    if current_view == set(view_rows):
        return
    col_last = max(0, view_model.columnCount() - 1)
    selection = item_selection_for_view_rows(view_model, view_rows, last_col=col_last)
    if selection.isEmpty():
        return
    table = parent_app.table
    was_programmatic = bool(getattr(parent_app, "_in_programmatic_table_selection", False))
    parent_app._in_programmatic_table_selection = True
    table.setUpdatesEnabled(False)
    try:
        sm.select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        anchor_col = 1 if col_last > 1 else 0
        idx = view_model.index(view_rows[0], anchor_col)
        sm.setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
        if scroll and idx.isValid():
            table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
    finally:
        table.setUpdatesEnabled(True)
        parent_app._in_programmatic_table_selection = was_programmatic
    table.viewport().update()


def clear_table_selection_from_plot(parent_app: ChemicalTableApp) -> None:
    """Clear Qt and large-selection override when the plot deselects."""
    parent_app._selected_oids_override = None
    table = parent_app.table
    table.clearSelection()
    table.viewport().update()
