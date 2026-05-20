from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from molmanager.ui.process_queue import ProcessQueueManager


def test_process_queue_blocked_while_render2d_batch(qapp):  # noqa: ARG001
    app = SimpleNamespace()
    app.render2d_batch_active = MagicMock(return_value=True)
    pq = ProcessQueueManager(qapp)
    pq._app = app
    pq.enqueue("Calculate descriptors", lambda ev: MagicMock())
    assert pq.has_pending_jobs()
    pq._maybe_start_next()
    assert pq.has_pending_jobs()
    assert not pq.has_running_job()

    app.render2d_batch_active = MagicMock(return_value=False)
    pq.schedule_resume()
    assert pq.has_running_job() or not pq.has_pending_jobs()
