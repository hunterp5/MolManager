"""Lazy PNG cache for structure-column 2D renders on very large tables."""

from __future__ import annotations

from collections import OrderedDict

from PyQt5.QtGui import QImage, QPixmap


class StructureRenderStore:
    """
    Holds rendered structure PNG bytes and decodes to QPixmap on demand.

    Only a bounded number of QPixmaps are kept in memory (LRU); the rest stay as bytes.
    """

    def __init__(self, *, max_decoded_pixmaps: int = 384) -> None:
        self._png: dict[int, bytes] = {}
        self._lru: OrderedDict[int, QPixmap] = OrderedDict()
        self._max_decoded = max(32, int(max_decoded_pixmaps))

    def clear(self) -> None:
        self._png.clear()
        self._lru.clear()

    def __len__(self) -> int:
        return len(self._png)

    def has_png(self, oid: int) -> bool:
        return int(oid) in self._png

    def remove_oid(self, oid: int) -> None:
        oid_i = int(oid)
        self._png.pop(oid_i, None)
        self._lru.pop(oid_i, None)

    def ingest_png(self, oid: int, png_bytes: bytes) -> None:
        oid_i = int(oid)
        self._png[oid_i] = bytes(png_bytes)
        self._lru.pop(oid_i, None)

    def ingest_batch(self, items: list[tuple[int, bytes]]) -> None:
        for oid, png_bytes in items:
            self.ingest_png(oid, png_bytes)

    def pixmap(self, oid: int) -> QPixmap | None:
        oid_i = int(oid)
        cached = self._lru.get(oid_i)
        if cached is not None and not cached.isNull():
            self._lru.move_to_end(oid_i)
            return cached
        raw = self._png.get(oid_i)
        if not raw:
            return None
        pm = QPixmap.fromImage(QImage.fromData(raw))
        if pm.isNull():
            return None
        self._lru[oid_i] = pm
        self._lru.move_to_end(oid_i)
        while len(self._lru) > self._max_decoded:
            self._lru.popitem(last=False)
        return pm

    def trim_decoded_cache(self, *, keep_oids: set[int] | None = None) -> None:
        """Drop decoded pixmaps not in *keep_oids* (PNG bytes are retained)."""
        if keep_oids is None:
            self._lru.clear()
            return
        keep = {int(x) for x in keep_oids}
        for oid in list(self._lru):
            if oid not in keep:
                del self._lru[oid]
