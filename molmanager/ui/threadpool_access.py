"""Resolve the app's QThreadPool (or global fallback) for background workers."""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import QRunnable, QThreadPool


def threadpool_for_app(app: Any) -> QThreadPool:
    """Return ``app.threadpool`` when present, else ``QThreadPool.globalInstance()``."""
    if app is None:
        return QThreadPool.globalInstance()
    pool = getattr(app, "threadpool", None)
    if pool is not None:
        return pool
    return QThreadPool.globalInstance()


def start_runnable_on_app_pool(app: Any, runnable: QRunnable) -> None:
    """``threadpool_for_app(app).start(runnable)``."""
    threadpool_for_app(app).start(runnable)
