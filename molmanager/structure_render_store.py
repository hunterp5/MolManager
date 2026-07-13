"""Lazy PNG cache for structure-column 2D renders on very large tables."""

from __future__ import annotations

from collections import OrderedDict

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap

from .display_constants import STRUCTURE_DEPICT_HEIGHT, STRUCTURE_DEPICT_WIDTH


class StructureRenderStore:
    """
    Holds rendered structure PNG bytes and decodes to QPixmap on demand.

    Only a bounded number of QPixmaps are kept in memory (LRU); the rest stay as bytes.
    Optionally caps total PNG entries (oldest insertion order evicted).
    """

    def __init__(
        self,
        *,
        max_decoded_pixmaps: int = 384,
        max_png_entries: int = 0,
    ) -> None:
        self._png: dict[int, bytes] = {}
        self._lru: OrderedDict[int, QPixmap] = OrderedDict()
        self._max_decoded = max(32, int(max_decoded_pixmaps))
        # 0 = unlimited PNG entry count
        self._max_png_entries = max(0, int(max_png_entries))

    def clear(self) -> None:
        self._png.clear()
        self._lru.clear()

    def __len__(self) -> int:
        return len(self._png)

    def has_png(self, oid: int) -> bool:
        return int(oid) in self._png

    def png_bytes(self, oid: int) -> bytes | None:
        raw = self._png.get(int(oid))
        return bytes(raw) if raw else None

    def remove_oid(self, oid: int) -> None:
        oid_i = int(oid)
        self._png.pop(oid_i, None)
        self._lru.pop(oid_i, None)

    def ingest_png(self, oid: int, png_bytes: bytes) -> None:
        oid_i = int(oid)
        if oid_i in self._png:
            # Re-insert so eviction order is refreshable
            del self._png[oid_i]
        self._png[oid_i] = bytes(png_bytes)
        self._lru.pop(oid_i, None)
        self._trim_png_entries()

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
        dw, dh = int(STRUCTURE_DEPICT_WIDTH), int(STRUCTURE_DEPICT_HEIGHT)
        if pm.width() > dw or pm.height() > dh:
            pm = pm.scaled(dw, dh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
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

    def _trim_png_entries(self) -> None:
        limit = self._max_png_entries
        if limit <= 0:
            return
        while len(self._png) > limit:
            # Insertion-ordered dict: drop oldest PNG bytes first.
            oid = next(iter(self._png))
            del self._png[oid]
            self._lru.pop(oid, None)
