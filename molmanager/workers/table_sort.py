"""Background worker that sorts a snapshot of the compound table off the GUI thread."""

from __future__ import annotations

import logging

from PyQt5.QtCore import QRunnable

from ..table_sort import build_sort_order

logger = logging.getLogger(__name__)


class TableSortWorker(QRunnable):
    """Sort ``(oid, raw_text)`` pairs and emit the ordered oids for the GUI thread to apply.

    ``generation`` lets the GUI ignore results from superseded sort requests (e.g. the user
    clicked another column before this finished).
    """

    def __init__(self, pairs, column, sort_kind, reverse, generation, signals):
        super().__init__()
        self._pairs = pairs
        self._column = int(column)
        self._sort_kind = sort_kind
        self._reverse = bool(reverse)
        self._generation = int(generation)
        self._signals = signals

    def run(self) -> None:
        try:
            ordered = build_sort_order(self._pairs, self._column, self._sort_kind, self._reverse)
        except Exception:
            logger.exception("Background table sort failed")
            ordered = [oid for oid, _ in self._pairs]
        try:
            self._signals.table_sorted.emit(self._generation, ordered)
        except Exception:
            pass
