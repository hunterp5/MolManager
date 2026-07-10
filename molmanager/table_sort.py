"""Qt-free sort-key logic for the compound table, shared by the model and the background worker.

Operates on lightweight ``(oid, raw_text)`` pairs so the expensive key parsing and sort can run on a
worker thread for large tables without touching Qt objects.
"""

from __future__ import annotations

from .utils import safe_float


def sort_key_for_pairs(column: int, sort_kind: str):
    """Return a key function over ``(oid, raw_text)`` items matching the table's sort semantics.

    ``sort_kind``: ``"numeric"`` (numbers first, then case-insensitive text), ``"alphabetic"``
    (case-insensitive text), or ``"auto"`` (numbers first using the raw cell text).
    """

    def key(item: tuple[int, str]):
        oid, raw = item
        if column == 0 or column == 1:
            if sort_kind == "alphabetic":
                return (0, str(oid))
            return (0, oid)
        raw = raw or ""
        if sort_kind == "alphabetic":
            return (1, raw.strip().lower())
        if sort_kind == "numeric":
            stripped = raw.strip()
            f = safe_float(stripped)
            return (0, float(f)) if f is not None else (1, stripped.lower())
        # auto: match the legacy behavior (no strip before parse / lower).
        f = safe_float(raw)
        return (0, float(f)) if f is not None else (1, raw.lower())

    return key


def build_sort_order(
    pairs: list[tuple[int, str]], column: int, sort_kind: str, reverse: bool
) -> list[int]:
    """Sort ``(oid, raw_text)`` pairs and return the ordered list of oids."""
    pairs.sort(key=sort_key_for_pairs(column, sort_kind), reverse=reverse)
    return [oid for oid, _ in pairs]
