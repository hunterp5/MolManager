"""Track short-lived threadpool work for the Processes dialog."""

from __future__ import annotations

from typing import Any


def register_background_job(app: Any, job_id: str, title: str) -> None:
    jobs = getattr(app, "_background_jobs", None)
    if jobs is None:
        jobs = {}
        app._background_jobs = jobs
    jobs[str(job_id)] = str(title or "Background job")
    hub = getattr(app, "background_activity", None)
    if hub is not None:
        hub.notify_changed()


def unregister_background_job(app: Any, job_id: str) -> None:
    jobs = getattr(app, "_background_jobs", None)
    if not jobs:
        return
    jobs.pop(str(job_id), None)
    hub = getattr(app, "background_activity", None)
    if hub is not None:
        hub.notify_changed()
