"""Bounded, disk-spilling store for the working RDKit ``Mol`` objects (``ChemicalTableApp.mols``).

Holding one live RDKit ``Mol`` per compound is the dominant RAM cost for very large tables. This
store keeps the most-recently-set molecules resident and spills the rest to a temporary SQLite
file as ``ToBinary`` blobs (lossless: conformers, coordinates, and properties are preserved). Spilled
molecules are rehydrated on demand, so RAM stays bounded while browsing large tables.

It emulates the subset of the ``dict`` API that ``ChemicalTableApp`` uses (``[]``, ``get``, ``in``,
iteration, ``items``, ``pop``, ``len``/truthiness). Access must stay on the GUI thread; background
workers receive materialized snapshots, never this live store.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from collections import OrderedDict

from rdkit import Chem

logger = logging.getLogger(__name__)

_MISSING = object()

try:
    _PROP_FLAGS = int(Chem.PropertyPickleOptions.AllProps)
except Exception:  # pragma: no cover - defensive: older RDKit
    _PROP_FLAGS = None


def _mol_to_blob(mol: Chem.Mol) -> bytes | None:
    if mol is None:
        return None
    try:
        if _PROP_FLAGS is not None:
            return mol.ToBinary(_PROP_FLAGS)
        return mol.ToBinary()
    except Exception:
        try:
            return mol.ToBinary()
        except Exception:
            return None


def _mol_from_blob(blob: bytes | None) -> Chem.Mol | None:
    if not blob:
        return None
    try:
        return Chem.Mol(bytes(blob))
    except Exception:
        return None


class LazyMolStore:
    """Dict-like ``oid -> Chem.Mol`` store with a bounded resident set and disk spill.

    ``_live`` (LRU by insertion/access) and ``_disk_oids`` are kept disjoint; each oid's molecule
    lives in exactly one tier. ``max_live <= 0`` disables spilling entirely (legacy behavior).
    """

    def __init__(self, *, max_live: int = 200_000) -> None:
        self._live: OrderedDict[int, Chem.Mol] = OrderedDict()
        self._max_live = max(0, int(max_live))
        self._disk: sqlite3.Connection | None = None
        self._disk_path: str | None = None
        self._disk_oids: set[int] = set()

    # --- disk spill backend -------------------------------------------------

    def _ensure_disk(self) -> sqlite3.Connection | None:
        if self._disk is not None:
            return self._disk
        try:
            fd, path = tempfile.mkstemp(prefix="molmanager_mols_", suffix=".sqlite")
            os.close(fd)
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("CREATE TABLE IF NOT EXISTS mols (oid INTEGER PRIMARY KEY, data BLOB)")
            conn.commit()
            self._disk = conn
            self._disk_path = path
        except Exception:
            logger.warning("Mol store: disk spill unavailable; keeping all molecules in RAM", exc_info=True)
            self._disk = None
            self._disk_path = None
        return self._disk

    def _disk_write(self, oid: int, blob: bytes) -> bool:
        conn = self._ensure_disk()
        if conn is None:
            return False
        try:
            conn.execute("INSERT OR REPLACE INTO mols (oid, data) VALUES (?, ?)", (int(oid), sqlite3.Binary(blob)))
            return True
        except Exception:
            logger.debug("Mol store: disk write failed for oid=%s", oid, exc_info=True)
            return False

    def _disk_read(self, oid: int) -> bytes | None:
        conn = self._disk
        if conn is None:
            return None
        try:
            cur = conn.execute("SELECT data FROM mols WHERE oid = ?", (int(oid),))
            row = cur.fetchone()
        except Exception:
            logger.debug("Mol store: disk read failed for oid=%s", oid, exc_info=True)
            return None
        return bytes(row[0]) if row and row[0] is not None else None

    def _disk_delete(self, oid: int) -> None:
        conn = self._disk
        if conn is None:
            return
        try:
            conn.execute("DELETE FROM mols WHERE oid = ?", (int(oid),))
        except Exception:
            logger.debug("Mol store: disk delete failed for oid=%s", oid, exc_info=True)

    def _enforce_cap(self) -> None:
        if self._max_live <= 0:
            return
        while len(self._live) > self._max_live:
            oid, mol = self._live.popitem(last=False)
            blob = _mol_to_blob(mol)
            if blob is not None and self._disk_write(oid, blob):
                self._disk_oids.add(oid)
            # If serialization/write fails (or mol is None), the entry is simply dropped.

    def _rehydrate(self, oid: int) -> Chem.Mol | None:
        """Return a spilled molecule without promoting it to RAM (avoids scroll/iteration churn)."""
        return _mol_from_blob(self._disk_read(oid))

    # --- dict-like API ------------------------------------------------------

    def __setitem__(self, oid: int, mol: Chem.Mol) -> None:
        oid = int(oid)
        self._disk_oids.discard(oid)
        self._live[oid] = mol
        self._live.move_to_end(oid)
        self._enforce_cap()

    def __getitem__(self, oid: int) -> Chem.Mol:
        oid = int(oid)
        mol = self._live.get(oid)
        if mol is not None:
            self._live.move_to_end(oid)
            return mol
        if oid in self._disk_oids:
            mol = self._rehydrate(oid)
            if mol is not None:
                return mol
        raise KeyError(oid)

    def get(self, oid, default=None):
        try:
            return self.__getitem__(oid)
        except KeyError:
            return default

    def get_blob(self, oid: int) -> bytes | None:
        """Return serialized mol bytes without promoting spilled entries into RAM."""
        oid = int(oid)
        live = self._live.get(oid)
        if live is not None:
            return _mol_to_blob(live)
        if oid in self._disk_oids:
            return self._disk_read(oid)
        return None

    def pop(self, oid, default=_MISSING):
        oid = int(oid)
        found = _MISSING
        if oid in self._live:
            found = self._live.pop(oid)
        elif oid in self._disk_oids:
            found = self._rehydrate(oid)
            self._disk_oids.discard(oid)
            self._disk_delete(oid)
        if found is _MISSING:
            if default is _MISSING:
                raise KeyError(oid)
            return default
        return found

    def __delitem__(self, oid: int) -> None:
        oid = int(oid)
        existed = False
        if oid in self._live:
            del self._live[oid]
            existed = True
        if oid in self._disk_oids:
            self._disk_oids.discard(oid)
            self._disk_delete(oid)
            existed = True
        if not existed:
            raise KeyError(oid)

    def __contains__(self, oid) -> bool:
        oid = int(oid)
        return oid in self._live or oid in self._disk_oids

    def __iter__(self):
        # Snapshot keys so callers can mutate the store while iterating oids.
        return iter(list(self._live.keys()) + list(self._disk_oids))

    def __len__(self) -> int:
        return len(self._live) + len(self._disk_oids)

    def keys(self):
        return list(self.__iter__())

    def items(self):
        """Yield ``(oid, mol)`` for every entry, rehydrating spilled molecules transiently."""
        for oid in list(self._live.keys()):
            mol = self._live.get(oid)
            if mol is not None:
                yield oid, mol
        for oid in list(self._disk_oids):
            mol = self._rehydrate(oid)
            if mol is not None:
                yield oid, mol

    def values(self):
        for _oid, mol in self.items():
            yield mol

    def clear(self) -> None:
        self._live.clear()
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
