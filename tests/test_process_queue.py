"""ProcessQueueManager: cancel must not drop queued jobs."""

from __future__ import annotations

import threading
import time

import pytest
from PyQt5.QtCore import QObject, QRunnable

from chemmanager.ui.process_queue import ProcessQueueManager


def test_cancel_running_preserves_queued_and_second_job_runs(qapp):
    parent = QObject()
    pq = ProcessQueueManager(parent)
    trace: list[str] = []

    class WaitsOnCancel(QRunnable):
        def __init__(self, cancel_ev: threading.Event):
            super().__init__()
            self._cancel_ev = cancel_ev

        def run(self) -> None:
            trace.append("A_start")
            self._cancel_ev.wait(timeout=60.0)
            trace.append("A_end")

    class FinishesQuick(QRunnable):
        def run(self) -> None:
            trace.append("B_ran")

    pq.enqueue("job A", lambda ev: WaitsOnCancel(ev))
    pq.enqueue("job B", lambda ev: FinishesQuick())

    for _ in range(300):
        qapp.processEvents()
        if "A_start" in trace and pq.queued_job_count() == 1:
            break
        time.sleep(0.01)

    assert "A_start" in trace, trace
    assert pq.queued_job_count() == 1, "second job should remain queued while A runs"

    assert pq.cancel_running() is True
    assert pq.queued_job_count() == 1, "cancel must not dequeue waiting tool jobs"

    for _ in range(500):
        qapp.processEvents()
        time.sleep(0.01)
        if "B_ran" in trace and "A_end" in trace:
            break

    assert "A_end" in trace, trace
    assert "B_ran" in trace, trace
    assert pq.queued_job_count() == 0
    assert pq.wait_for_all_jobs(60000)


def test_remove_queued_job_unknown_returns_false(qapp):  # noqa: ARG001
    parent = QObject()
    pq = ProcessQueueManager(parent)
    assert pq.remove_queued_job("no_such_id") is False


def test_remove_queued_job_removes_waiting_job(qapp):
    parent = QObject()
    pq = ProcessQueueManager(parent)
    hold = threading.Event()

    class Hold(QRunnable):
        def __init__(self, ev: threading.Event):
            super().__init__()
            self._ev = ev

        def run(self) -> None:
            self._ev.wait(timeout=30.0)

    class Quick(QRunnable):
        def run(self) -> None:
            pass

    pq.enqueue("hold", lambda ev: Hold(hold))
    for _ in range(200):
        qapp.processEvents()
        time.sleep(0.01)
        if pq.queued_job_count() == 0:
            break
    j_tail = pq.enqueue("tail", lambda ev: Quick())
    assert pq.queued_job_count() == 1
    assert pq.remove_queued_job(j_tail) is True
    assert pq.queued_job_count() == 0
    hold.set()
    assert pq.wait_for_all_jobs(10000)
