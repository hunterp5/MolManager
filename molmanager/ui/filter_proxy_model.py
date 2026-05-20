"""Proxy model for scalable table visibility filtering."""

from __future__ import annotations

from PyQt5.QtCore import QSortFilterProxyModel


class FilterProxyModel(QSortFilterProxyModel):
    """Filter rows by source-model OID membership."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._visible_oids: frozenset[int] | None = None

    def set_visible_oids(self, oids: frozenset[int] | None) -> None:
        new_oids = None if oids is None else frozenset(int(x) for x in oids)
        if new_oids == self._visible_oids:
            return
        self._visible_oids = new_oids
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:  # noqa: N802
        if self._visible_oids is None:
            return True
        src = self.sourceModel()
        if src is None:
            return True
        try:
            oid = int(src.row_oid(source_row))
        except Exception:
            return False
        return oid in self._visible_oids

