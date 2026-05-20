"""Thread-safe tool progress for background workers (polled on the Qt GUI thread)."""

from __future__ import annotations

import threading


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
