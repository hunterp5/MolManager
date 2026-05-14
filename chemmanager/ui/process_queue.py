"""Serial queue for long-running tool jobs (conformers, descriptors, export, load, …)."""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)


@dataclass
class _QueuedJob:
    job_id: str
    title: str
    factory: Callable[[threading.Event], QRunnable]


class ProcessQueueManager(QObject):
    """
    Runs at most one queued tool job at a time on a **dedicated** single-thread pool (not
    ``app.threadpool``), so substructure filters, renders, and other pool users cannot interleave
    with the queue runner or reorder completion relative to cancellation.

    Jobs are built with ``factory(cancel_event)`` so cooperative cancellation can be wired per task.
    """

    snapshot_changed = pyqtSignal()
    thread_finished = pyqtSignal(str)

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._app = app
        self._queue: deque[_QueuedJob] = deque()
        self._busy = False
        self._current_job_id: str | None = None
        self._current_title: str | None = None
        self._running_cancel: threading.Event | None = None
        self.thread_finished.connect(self._on_job_thread_finished, Qt.QueuedConnection)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

    def _threadpool(self) -> QThreadPool:
        return self._pool

    def enqueue(self, title: str, factory: Callable[[threading.Event], QRunnable]) -> str:
        """Queue a job; returns job id. ``factory`` receives a fresh cancel event for this run."""
        job_id = str(uuid.uuid4())[:8]
        self._queue.append(_QueuedJob(job_id=job_id, title=title.strip() or "Job", factory=factory))
        self.snapshot_changed.emit()
        self._maybe_start_next()
        return job_id

    def cancel_running(self) -> bool:
        """Cooperatively cancel the currently running job, if any. Returns True if a cancel was signaled.

        Does **not** remove jobs still waiting in ``_queue``; only ``clear_queued`` does that.
        """
        ev = self._running_cancel
        if ev is None or ev.is_set():
            return False
        ev.set()
        self.snapshot_changed.emit()
        return True

    def queued_job_count(self) -> int:
        """Number of jobs waiting to start (excludes the job currently running, if any)."""
        return len(self._queue)

    def wait_for_all_jobs(self, msec: int = 60000) -> bool:
        """Wait until no queued-tool runner is active on the internal pool (mainly for tests)."""
        return self._pool.waitForDone(msec)

    def clear_queued(self) -> int:
        """Remove all jobs waiting in the queue (not the running job). Returns number removed."""
        n = len(self._queue)
        self._queue.clear()
        if n:
            self.snapshot_changed.emit()
        return n

    def remove_queued_job(self, job_id: str) -> bool:
        """Remove a single waiting job by id. Returns True if a job was removed."""
        for i, job in enumerate(self._queue):
            if job.job_id == job_id:
                del self._queue[i]
                self.snapshot_changed.emit()
                return True
        return False

    def snapshot(self) -> dict[str, Any]:
        """Serializable view for the Processes dialog."""
        queued = [{"job_id": j.job_id, "title": j.title, "status": "Queued"} for j in self._queue]
        running = None
        if self._busy and self._current_job_id:
            running = {
                "job_id": self._current_job_id,
                "title": self._current_title or "",
                "status": "Running",
                "cancellable": self._running_cancel is not None and not self._running_cancel.is_set(),
            }
        return {"running": running, "queued": queued}

    @pyqtSlot(str)
    def _on_job_thread_finished(self, job_id: str) -> None:
        if self._current_job_id != job_id:
            return
        self._busy = False
        self._current_job_id = None
        self._current_title = None
        self._running_cancel = None
        self.snapshot_changed.emit()
        self._maybe_start_next()

    def _maybe_start_next(self) -> None:
        if self._busy or not self._queue:
            return
        job = self._queue.popleft()
        cancel_ev = threading.Event()
        self._busy = True
        self._current_job_id = job.job_id
        self._current_title = job.title
        self._running_cancel = cancel_ev
        try:
            inner = job.factory(cancel_ev)
        except Exception:
            logger.exception("Job factory failed (job_id=%s)", job.job_id)
            self._busy = False
            self._current_job_id = None
            self._current_title = None
            self._running_cancel = None
            self.snapshot_changed.emit()
            self._maybe_start_next()
            return
        self.snapshot_changed.emit()
        self._threadpool().start(_QueueJobRunner(self, job.job_id, inner))


class _QueueJobRunner(QRunnable):
    """Runs one inner QRunnable on the pool thread, then notifies the manager on the GUI thread."""

    def __init__(self, manager: ProcessQueueManager, job_id: str, inner: QRunnable):
        super().__init__()
        self._manager = manager
        self._job_id = job_id
        self._inner = inner

    def run(self) -> None:
        try:
            self._inner.run()
        except Exception:
            logger.exception("Queued job crashed (job_id=%s)", self._job_id)
        finally:
            self._manager.thread_finished.emit(self._job_id)
