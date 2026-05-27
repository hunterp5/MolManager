"""Background job registry for Processes dialog."""

from __future__ import annotations

from types import SimpleNamespace

from molmanager.ui.background_jobs import register_background_job, unregister_background_job


def test_background_job_register_unregister() -> None:
    app = SimpleNamespace(_background_jobs={}, background_activity=SimpleNamespace(notify_calls=0))

    def notify() -> None:
        app.background_activity.notify_calls += 1

    app.background_activity.notify_changed = notify

    register_background_job(app, "job-a", "Test job")
    assert app._background_jobs == {"job-a": "Test job"}
    assert app.background_activity.notify_calls == 1

    unregister_background_job(app, "job-a")
    assert app._background_jobs == {}
    assert app.background_activity.notify_calls == 2
