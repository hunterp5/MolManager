"""Lazy PNG cache for structure-column 2D renders on very large tables.

PNG bytes are held in a bounded in-memory LRU; once that cap is exceeded, the oldest bytes spill
to a temporary on-disk SQLite store. Re-display reads the bytes back and decodes them (cheap) — no
re-render is required — so RAM stays flat regardless of table size while scrolling remains fast.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from collections import OrderedDict

from PyQt5.QtGui import QImage, QPixmap

logger = logging.getLogger(__name__)


class StructureRenderStore:
    """
    Holds rendered structure PNG bytes and decodes to QPixmap on demand.

    Only a bounded number of QPixmaps are kept decoded (LRU); PNG bytes are kept in a bounded RAM
    LRU and spilled to a temp SQLite file beyond that cap. ``_png`` keys and ``_disk_oids`` are
    always disjoint (each oid's bytes live in exactly one place).
    """

    def __init__(self, *, max_decoded_pixmaps: int = 384, max_png_ram_rows: int = 50_000) -> None:
        self._png: OrderedDict[int, bytes] = OrderedDict()
        self._lru: OrderedDict[int, QPixmap] = OrderedDict()
        self._max_decoded = max(32, int(max_decoded_pixmaps))
        # 0 (or negative) disables disk spill: all PNG bytes stay resident (legacy behavior).
        self._max_png_ram = max(0, int(max_png_ram_rows))
        self._disk: sqlite3.Connection | None = None
        self._disk_path: str | None = None
        self._disk_oids: set[int] = set()

    # --- disk spill backend -------------------------------------------------

    def _ensure_disk(self) -> sqlite3.Connection | None:
        if self._disk is not None:
            return self._disk
        try:
            fd, path = tempfile.mkstemp(prefix="molmanager_render_", suffix=".sqlite")
            os.close(fd)
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("CREATE TABLE IF NOT EXISTS png (oid INTEGER PRIMARY KEY, data BLOB)")
            conn.commit()
            self._disk = conn
            self._disk_path = path
        except Exception:
            logger.warning("Structure render store: disk spill unavailable; keeping PNGs in RAM", exc_info=True)
            self._disk = None
            self._disk_path = None
        return self._disk

    def _disk_write(self, oid: int, data: bytes) -> bool:
        conn = self._ensure_disk()
        if conn is None:
            return False
        try:
            conn.execute("INSERT OR REPLACE INTO png (oid, data) VALUES (?, ?)", (int(oid), sqlite3.Binary(data)))
            return True
        except Exception:
            logger.debug("Structure render store: disk write failed for oid=%s", oid, exc_info=True)
            return False

    def _disk_read(self, oid: int) -> bytes | None:
        conn = self._disk
        if conn is None:
            return None
        try:
            cur = conn.execute("SELECT data FROM png WHERE oid = ?", (int(oid),))
            row = cur.fetchone()
        except Exception:
            logger.debug("Structure render store: disk read failed for oid=%s", oid, exc_info=True)
            return None
        return bytes(row[0]) if row and row[0] is not None else None

    def _disk_delete(self, oid: int) -> None:
        conn = self._disk
        if conn is None:
            return
        try:
            conn.execute("DELETE FROM png WHERE oid = ?", (int(oid),))
        except Exception:
            logger.debug("Structure render store: disk delete failed for oid=%s", oid, exc_info=True)

    def _enforce_ram_cap(self) -> None:
        """Spill oldest PNG bytes to disk until the RAM LRU is within its cap."""
        if self._max_png_ram <= 0:
            return
        while len(self._png) > self._max_png_ram:
            oid, data = self._png.popitem(last=False)
            if self._disk_write(oid, data):
                self._disk_oids.add(oid)
            # If the write failed, the bytes are dropped; the cell will show blank until re-render.

    # --- public API ---------------------------------------------------------

    def clear(self) -> None:
        self._png.clear()
        self._lru.clear()
        self._disk_oids.clear()
        conn = self._disk
        self._disk = None
        path = self._disk_path
        self._disk_path = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    def __len__(self) -> int:
        return len(self._png) + len(self._disk_oids)

    def has_png(self, oid: int) -> bool:
        oid_i = int(oid)
        return oid_i in self._png or oid_i in self._disk_oids

    def remove_oid(self, oid: int) -> None:
        oid_i = int(oid)
        self._png.pop(oid_i, None)
        self._lru.pop(oid_i, None)
        if oid_i in self._disk_oids:
            self._disk_oids.discard(oid_i)
            self._disk_delete(oid_i)

    def ingest_png(self, oid: int, png_bytes: bytes) -> None:
        oid_i = int(oid)
        # New bytes supersede any spilled copy; keep RAM/disk sets disjoint.
        self._disk_oids.discard(oid_i)
        self._png[oid_i] = bytes(png_bytes)
        self._png.move_to_end(oid_i)
        self._lru.pop(oid_i, None)
        self._enforce_ram_cap()

    def ingest_batch(self, items: list[tuple[int, bytes]]) -> None:
        for oid, png_bytes in items:
            self.ingest_png(oid, png_bytes)

    def _bytes_for(self, oid: int) -> bytes | None:
        oid_i = int(oid)
        raw = self._png.get(oid_i)
        if raw is not None:
            self._png.move_to_end(oid_i)
            return raw
        if oid_i in self._disk_oids:
            # Decode from disk without promoting to RAM: the decoded-pixmap LRU already covers the
            # visible region, so promotion would only cause spill-write churn during scrolling.
            return self._disk_read(oid_i)
        return None

    def pixmap(self, oid: int) -> QPixmap | None:
        oid_i = int(oid)
        cached = self._lru.get(oid_i)
        if cached is not None and not cached.isNull():
            self._lru.move_to_end(oid_i)
            return cached
        raw = self._bytes_for(oid_i)
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
