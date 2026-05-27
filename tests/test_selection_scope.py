"""selection_scope_checked respects parent_app on docked tool panels."""

from __future__ import annotations

from types import SimpleNamespace

from molmanager.ui.dialogs.scope import selection_scope_checked


def test_selection_scope_checked_uses_parent_app_not_dock_parent() -> None:
    host = SimpleNamespace(_rows=[0, 1])
    host._selected_logical_rows = lambda: list(host._rows)
    dock = object()
    dlg = SimpleNamespace(
        parent_app=host,
        parent=lambda: dock,
        only_selected_cb=SimpleNamespace(isChecked=lambda: True),
        _have_selection=False,
    )
    assert selection_scope_checked(dlg) is True
    host._rows = []
    assert selection_scope_checked(dlg) is False
