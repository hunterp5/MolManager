"""Session in-memory cache of pkasolver microstates (structure key → picklable states).

Keyed by :func:`molmanager.workers.structure_grouping.structure_key` (canonical SMILES).
Used so Predict pKa and descriptor jobs (LogD/LogS 7.4, CNS MPO, …) share one GNN pass
per unique structure within the same app session. Cleared on Clear / shutdown.
Does not persist to SDF/CSV exports.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
# Present keys may map to ``None`` (valid empty / non-applicable prediction).
_store: dict[str, list[Any] | None] = {}


def lookup(structure_key: str) -> tuple[bool, list[Any] | None]:
    """
    Return ``(True, states)`` on a cache hit (``states`` may be ``None``).

    Return ``(False, None)`` on a miss.
    """
    key = str(structure_key or "")
    if not key:
        return False, None
    with _lock:
        if key not in _store:
            return False, None
        return True, _store[key]


def store(structure_key: str, states: list[Any] | None) -> None:
    """Write-through a picklable microstate list (or ``None`` for empty / N/A)."""
    key = str(structure_key or "")
    if not key:
        return
    with _lock:
        _store[key] = states


def store_many(items: dict[str, list[Any] | None]) -> None:
    if not items:
        return
    with _lock:
        for key, states in items.items():
            k = str(key or "")
            if k:
                _store[k] = states


def clear() -> None:
    with _lock:
        _store.clear()


def size() -> int:
    with _lock:
        return len(_store)
