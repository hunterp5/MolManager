"""Shared helpers for plots docked beside the main compound table."""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget


def iter_plot_selection_views(root: QWidget | None) -> list:
    """Return widgets that implement ``sync_from_table_selection`` (plot ↔ table)."""
    if root is None:
        return []
    views: list = []
    seen: set[int] = set()

    def add(candidate) -> None:
        if candidate is None:
            return
        key = id(candidate)
        if key in seen:
            return
        if not callable(getattr(candidate, "sync_from_table_selection", None)):
            return
        seen.add(key)
        views.append(candidate)

    add(root)
    add(getattr(root, "_plot_view", None))
    return views


def is_dockable_plot_widget(widget) -> bool:
    """True when the widget can be placed in the main-window plot panel."""
    return (
        getattr(widget, "only_selected_cb", None) is not None
        and bool(iter_plot_selection_views(widget))
    )
