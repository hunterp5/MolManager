"""Helpers for building Qt item selections from row indices (merged contiguous ranges)."""

from __future__ import annotations

from PyQt5.QtCore import QItemSelection


def merge_sorted_row_indices(rows: list[int]) -> list[tuple[int, int]]:
    """Merge sorted unique row indices into inclusive ``(lo, hi)`` spans."""
    if not rows:
        return []
    uniq = sorted({int(r) for r in rows})
    spans: list[tuple[int, int]] = []
    lo = hi = uniq[0]
    for r in uniq[1:]:
        if r == hi + 1:
            hi = r
        else:
            spans.append((lo, hi))
            lo = hi = r
    spans.append((lo, hi))
    return spans


def item_selection_for_view_rows(view_model, view_rows: list[int], *, last_col: int) -> QItemSelection:
    """Build a ``QItemSelection`` from proxy/view row indices using contiguous ranges."""
    selection = QItemSelection()
    for lo, hi in merge_sorted_row_indices(view_rows):
        top = view_model.index(lo, 0)
        bottom = view_model.index(hi, last_col)
        if top.isValid() and bottom.isValid():
            selection.select(top, bottom)
    return selection
