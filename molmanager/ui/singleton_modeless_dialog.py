"""Shared helpers for one-at-a-time modeless tool windows (plotter, sketcher, etc.)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt5.QtWidgets import QWidget


def reuse_or_show_modeless_singleton(
    host: Any,
    attr_name: str,
    factory: Callable[[], QWidget],
    on_destroyed: Callable[[], None],
    *,
    on_reused_visible: Callable[[QWidget], None] | None = None,
) -> QWidget:
    """
    If ``getattr(host, attr_name)`` is a live widget, ``show()`` / ``raise_()`` / ``activateWindow()``
    and optionally ``on_reused_visible(dlg)``. Otherwise create with ``factory()``, assign it,
    connect ``destroyed`` to ``on_destroyed``, and ``show()``.

    Reuses the same instance even when it is **not visible** (e.g. minimized or hidden after
    ``close()`` without ``WA_DeleteOnClose``), so a long-running tool job is not orphaned when
    the user reopens the menu action.

    ``factory`` should return a fully configured dialog (modal flags, signals, etc.) before show.
    """
    dlg = getattr(host, attr_name, None)
    if dlg is not None:
        try:
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            if on_reused_visible is not None:
                on_reused_visible(dlg)
            return dlg
        except RuntimeError:
            setattr(host, attr_name, None)
    w = factory()
    setattr(host, attr_name, w)
    w.destroyed.connect(on_destroyed)
    w.show()
    return w
