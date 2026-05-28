"""Session in-memory cache of RDKit fingerprint bit vectors (OID + spec key)."""

from __future__ import annotations

import threading
from typing import Any

from rdkit import Chem

from .rdkit_fingerprints import _compute_fingerprint, spec_for_internal_key

_lock = threading.Lock()
_store: dict[tuple[int, str], Any] = {}


def get(oid: int, internal_key: str) -> Any | None:
    with _lock:
        return _store.get((int(oid), str(internal_key)))


def store(oid: int, internal_key: str, fp: Any) -> None:
    if fp is None:
        return
    with _lock:
        _store[(int(oid), str(internal_key))] = fp


def store_from_mol(oid: int, internal_key: str, mol: Chem.Mol | None) -> Any | None:
    spec = spec_for_internal_key(internal_key)
    if spec is None or mol is None:
        return None
    fp = _compute_fingerprint(mol, spec)
    if fp is not None:
        store(oid, internal_key, fp)
    return fp


def clear() -> None:
    with _lock:
        _store.clear()
