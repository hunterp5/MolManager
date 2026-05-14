"""Shared helpers for tool dialogs (selection scope, etc.)."""

from __future__ import annotations

from PyQt5.QtWidgets import QDialog


def selection_scope_checked(dialog: QDialog) -> bool:
    """
    True when the dialog's “only selected rows” scope checkbox is checked and the parent
    main window currently has at least one selected table row.
    """
    cb = getattr(dialog, "only_selected_cb", None)
    if cb is None or not cb.isChecked():
        return False
    host = dialog.parent()
    if host is not None and hasattr(host, "_selected_logical_rows"):
        return len(host._selected_logical_rows()) > 0
    return bool(getattr(dialog, "_have_selection", False))
