"""Shared table ↔ Plotly selection helpers (Plotter and PlotlyInteractiveView)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtCore import QItemSelection, QItemSelectionModel, Qt, QTimer
from PyQt5.QtWidgets import QAbstractItemView

from .table_selection import item_selection_for_view_rows

if TYPE_CHECKING:
    from .main_window import ChemicalTableApp


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


def apply_table_selection_for_source_rows(parent_app: ChemicalTableApp, source_rows: list[int]) -> None:
    """Select visible proxy rows for source-model row indices (plot lasso / click)."""
    if not source_rows:
        return
    source_model = parent_app._table_model
    sm = parent_app.table.selectionModel()
    if sm is None:
        return
    view_model = parent_app.table.model()
    if view_model is None:
        return
    view_rows = parent_app._source_rows_to_view_rows(sorted(set(int(r) for r in source_rows)))
    if not view_rows:
        return
    col_last = max(0, view_model.columnCount() - 1)
    selection = item_selection_for_view_rows(view_model, view_rows, last_col=col_last)
    if selection.isEmpty():
        return
    table = parent_app.table
    table.setUpdatesEnabled(False)
    try:
        sm.select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
    finally:
        table.setUpdatesEnabled(True)
    anchor_col = 1 if col_last > 1 else 0
    idx = view_model.index(view_rows[0], anchor_col)
    sm.setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
    table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
    QTimer.singleShot(
        0,
        lambda: (
            parent_app.activateWindow(),
            table.setFocus(Qt.OtherFocusReason),
            table.viewport().update(),
        ),
    )


def clear_table_selection_from_plot(parent_app: ChemicalTableApp) -> None:
    """Clear Qt and large-selection override when the plot deselects."""
    parent_app._selected_oids_override = None
    table = parent_app.table
    table.clearSelection()
    table.viewport().update()
