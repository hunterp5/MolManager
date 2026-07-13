"""Background rebuild of the SQLite table mirror."""

from __future__ import annotations

import logging

from PyQt5.QtCore import QRunnable

from ..storage.sqlite_table_store import SqliteTableStore
from .signals import SqliteRebuildSignals

logger = logging.getLogger(__name__)


class SqliteRebuildWorker(QRunnable):
    """Build a fresh SQLite mirror off the UI thread.

    Prefer *db_path* already filled via streamed inserts; then only the oid index
    is created here. Legacy callers may still pass *entries* for a full rebuild.
    """

    def __init__(
        self,
        job_gen: int,
        headers: list[str],
        entries: list[tuple[int, dict[str, str]]] | None,
        db_path: str,
        signals: SqliteRebuildSignals,
        *,
        stream_finalize: bool = False,
    ) -> None:
        super().__init__()
        self.job_gen = job_gen
        self.headers = headers
        self.entries = entries
        self.db_path = db_path
        self.signals = signals
        self.stream_finalize = bool(stream_finalize)

    def run(self) -> None:
        try:
            store = SqliteTableStore(self.db_path)
            if self.stream_finalize:
                store.finish_stream_rebuild()
            else:
                store.rebuild(self.headers, list(self.entries or []))
            store.close()
            self.signals.finished.emit(self.job_gen, self.db_path)
        except Exception as e:
            logger.exception("SqliteRebuildWorker failed")
            self.signals.failed.emit(self.job_gen, str(e))
