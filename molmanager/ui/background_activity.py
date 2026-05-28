"""Unified signals and helpers for background work (process queue, Render 2D, Smina, …)."""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal


class BackgroundActivityHub(QObject):
    """
    Single ``changed`` signal for anything that should refresh the Processes dialog
    or other observers. Relays ``ProcessQueueManager.snapshot_changed`` and accepts
    explicit ``notify_changed()`` for activity outside the queue (e.g. Render 2D, Smina).
    """

    changed = pyqtSignal()

    def __init__(self, app: Any, parent: QObject | None = None):
        super().__init__(parent or app)
        self._app = app

    def attach(self) -> None:
        pq = getattr(self._app, "process_queue", None)
        if pq is not None:
            pq.snapshot_changed.connect(self.changed.emit)

    def notify_changed(self) -> None:
        self.changed.emit()

    def render2d_batch_active(self) -> bool:
        fn = getattr(self._app, "render2d_batch_active", None)
        return bool(fn()) if callable(fn) else False

    def smina_dock_active(self) -> bool:
        fn = getattr(self._app, "smina_dock_active", None)
        return bool(fn()) if callable(fn) else False

    def processes_view_rows(
        self,
    ) -> tuple[list[tuple[str, str, str]], list[dict[str, Any]]]:
        """Table rows for Processes: ``(status, job_id, title)`` and matching row metadata dicts."""
        pq = getattr(self._app, "process_queue", None)
        if pq is None:
            return [], []
        snap = pq.snapshot()
        rows: list[tuple[str, str, str]] = []
        metas: list[dict[str, Any]] = []
        r: dict | None = snap.get("running")
        if r:
            rows.append((r.get("status", ""), r.get("job_id", ""), r.get("title", "")))
            metas.append(
                {
                    "kind": "pq_running",
                    "job_id": r.get("job_id", ""),
                    "cancellable": bool(r.get("cancellable")),
                    "started_at": r.get("started_at"),
                }
            )
        for q in snap.get("queued") or []:
            rows.append((q.get("status", "Queued"), q.get("job_id", ""), q.get("title", "")))
            metas.append(
                {
                    "kind": "pq_queued",
                    "job_id": q.get("job_id", ""),
                    "enqueued_at": q.get("enqueued_at"),
                }
            )
        for fr in snap.get("fast_running") or []:
            rows.append((fr.get("status", "Running"), fr.get("job_id", ""), fr.get("title", "Interactive job")))
            metas.append(
                {
                    "kind": "pq_fast_running",
                    "job_id": fr.get("job_id", ""),
                    "cancellable": bool(fr.get("cancellable", True)),
                    "started_at": fr.get("started_at"),
                }
            )

        if self.render2d_batch_active():
            rows.insert(0, ("Running", "(render-2d)", "Render 2D — drawing structures…"))
            metas.insert(0, {"kind": "render2d"})

        if self.smina_dock_active():
            rows.insert(0, ("Running", "(smina)", "Dock (Smina) — smina"))
            metas.insert(0, {"kind": "smina"})

        for job_id, title in sorted((getattr(self._app, "_background_jobs", None) or {}).items()):
            rows.append(("Running", job_id, title))
            metas.append({"kind": "background", "job_id": job_id})

        return rows, metas

    def try_cancel_row(self, meta: dict | None) -> tuple[tuple[str, str] | None, str | None]:
        """
        Attempt cancel/remove for a Processes table row.

        Returns ``(dialog_info, status_text)`` where ``dialog_info`` is ``(title, text)``
        for ``QMessageBox.information`` when the action could not proceed or needs a notice;
        ``status_text`` is set on success for the status bar.
        """
        app = self._app
        pq = getattr(app, "process_queue", None)
        if not meta:
            return (("Cancel", "Select a process in the table first."), None)

        kind = meta.get("kind")
        if kind == "render2d":
            cancel = getattr(app, "cancel_render_2d_batch", None)
            if callable(cancel) and cancel():
                return (None, "Render 2D cancelled.")
            return (("Cancel", "Render 2D is not active."), None)

        if kind == "smina":
            cancel = getattr(app, "cancel_smina_dock", None)
            if callable(cancel) and cancel():
                return (None, "Smina stopped.")
            return (("Cancel", "Smina is not running."), None)

        if kind == "pq_running":
            run = pq.snapshot().get("running") if pq else None
            if not run or run.get("job_id") != meta.get("job_id"):
                return (("Cancel", "That job is no longer running."), None)
            if pq and pq.cancel_running():
                return (None, "Cancelling…")
            return (
                (
                    "Cancel",
                    "This job cannot be cancelled cooperatively, or a cancel was already requested.",
                ),
                None,
            )

        if kind == "pq_queued":
            jid = meta.get("job_id") or ""
            if pq and pq.remove_queued_job(jid):
                return (None, f"Removed queued job ({jid}).")
            return (("Cancel", "That job is no longer in the queue."), None)

        if kind == "pq_fast_running":
            jid = meta.get("job_id") or ""
            if pq and pq.cancel_fast_job(jid):
                return (None, "Cancelling interactive job…")
            return (("Cancel", "That interactive job is no longer running."), None)

        return (("Cancel", "Unknown row type."), None)

    def clear_queued_jobs(self) -> int:
        pq = getattr(self._app, "process_queue", None)
        return pq.clear_queued() if pq is not None else 0

    def prepare_for_quit(self) -> None:
        """Cooperatively stop background jobs before draining thread pools (window close)."""
        app = self._app
        if hasattr(app, "_invalidate_substructure_async_jobs"):
            app._invalidate_substructure_async_jobs()
        if hasattr(app, "cancel_render_2d_batch"):
            app.cancel_render_2d_batch()
        if hasattr(app, "cancel_smina_dock"):
            app.cancel_smina_dock()
        pq = getattr(app, "process_queue", None)
        if pq is not None:
            shutdown = getattr(pq, "shutdown_for_exit", None)
            if callable(shutdown):
                shutdown()
            else:
                pq.clear_queued()
                pq.cancel_running()
