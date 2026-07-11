"""Background filter OID fetch worker (SQLite pushdown)."""

from __future__ import annotations

import logging
from typing import Any

from PyQt5.QtCore import QRunnable

from ..filter_compute import fetch_matching_oids
from ..tool_progress import ToolProgressState, report_tool_progress
from .signals import FilterApplySignals

logger = logging.getLogger(__name__)


class FilterApplyWorker(QRunnable):
    """Compute filter-matched OIDs off the UI thread via SQLite."""

    def __init__(
        self,
        job_gen: int,
        db_path: str,
        where_sql: str,
        args: tuple,
        page_size: int,
        signals: FilterApplySignals,
        *,
        progress_state: ToolProgressState | None = None,
        worker_signals: Any = None,
    ) -> None:
        super().__init__()
        self.job_gen = job_gen
        self.db_path = db_path
        self.where_sql = where_sql
        self.args = args
        self.page_size = page_size
        self.signals = signals
        self.progress_state = progress_state
        self.worker_signals = worker_signals
        self._progress_throttle = [0, 0.0]

    def run(self) -> None:
        try:
            def _progress(done: int, total: int) -> None:
                report_tool_progress(
                    message="Applying filters…",
                    done=done,
                    total=total,
                    progress_state=self.progress_state,
                    signals=self.worker_signals,
                    throttle=self._progress_throttle,
                )

            oids = fetch_matching_oids(
                self.db_path,
                self.where_sql,
                self.args,
                page_size=self.page_size,
                progress_cb=_progress,
            )
            self.signals.finished.emit(self.job_gen, oids)
        except Exception as e:
            logger.exception("FilterApplyWorker failed")
            self.signals.failed.emit(self.job_gen, str(e))
