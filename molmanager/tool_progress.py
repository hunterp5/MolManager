"""Thread-safe tool progress for background workers (polled on the Qt GUI thread)."""

from __future__ import annotations

import threading
import time
from typing import Any


class ToolProgressState:
    """
    Updated from worker threads; read from a QTimer on the main window.

    Avoids relying on ``pyqtSignal`` delivery while the GIL is held by descriptor work.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._message = ""
        self._done = 0
        self._total = 1
        self._active = False

    def begin(self, message: str, total: int) -> None:
        with self._lock:
            self._message = str(message or "")
            self._done = 0
            self._total = max(1, int(total))
            self._active = True

    def update(self, message: str, done: int, total: int | None = None) -> None:
        with self._lock:
            self._message = str(message or "")
            self._done = max(0, int(done))
            if total is not None:
                self._total = max(1, int(total))

    def end(self) -> None:
        with self._lock:
            self._active = False

    def snapshot(self) -> tuple[str, int, int, bool]:
        with self._lock:
            return (self._message, self._done, self._total, self._active)


def report_tool_progress(
    *,
    message: str,
    done: int,
    total: int,
    progress_state: ToolProgressState | None = None,
    signals: Any = None,
    throttle: list | None = None,
    force_signal: bool = False,
) -> None:
    """
    Update polled status (``ToolProgressState``) and optionally emit ``tool_progress``.

    Workers should call this (or pass ``progress_state`` into helpers that do) so the
    bottom-left status bar stays current even when the GIL blocks Qt signal delivery.
    """
    tot = max(1, int(total))
    d = min(max(int(done), 0), tot)
    msg = str(message or "")
    if progress_state is not None:
        progress_state.update(msg, d, tot)
    if signals is None:
        return
    emit = force_signal
    if not emit and throttle is not None:
        now = time.monotonic()
        last_d, last_t = int(throttle[0]), float(throttle[1])
        step = max(1, tot // 20)
        if d == 0 or d >= tot or d - last_d >= step or (now - last_t) >= 0.25:
            throttle[0] = d
            throttle[1] = now
            emit = True
    elif not emit:
        emit = True
    if emit:
        try:
            signals.tool_progress.emit(msg, d, tot)
        except Exception:
            pass
