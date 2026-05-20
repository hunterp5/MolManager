"""Background rebuild of the SQLite table mirror."""

from __future__ import annotations

import logging

from PyQt5.QtCore import QRunnable

from ..storage.sqlite_table_store import SqliteTableStore
from .signals import SqliteRebuildSignals

logger = logging.getLogger(__name__)


class SqliteRebuildWorker(QRunnable):
    """Build a fresh SQLite mirror off the UI thread."""

    def __init__(
        self,
        job_gen: int,
        headers: list[str],
        entries: list[tuple[int, dict[str, str]]],
        db_path: str,
        signals: SqliteRebuildSignals,
    ) -> None:
        super().__init__()
        self.job_gen = job_gen
        self.headers = headers
        self.entries = entries
        self.db_path = db_path
        self.signals = signals

    def run(self) -> None:
        try:
            store = SqliteTableStore(self.db_path)
            store.rebuild(self.headers, self.entries)
            store.close()
            self.signals.finished.emit(self.job_gen, self.db_path)
        except Exception as e:
            logger.exception("SqliteRebuildWorker failed")
            self.signals.failed.emit(self.job_gen, str(e))
