"""Detect and reuse precomputed descriptor columns and fingerprint cache entries."""

from __future__ import annotations

from collections.abc import Callable, Iterable

_INVALID_CELLS = frozenset({"", "n/a", "na", "none", "-"})


def is_valid_descriptor_cell(value: str | None) -> bool:
    """True when a table cell holds a usable descriptor value."""
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    return text.lower() not in _INVALID_CELLS


def column_complete_for_oids(
    column: str,
    oids: Iterable[int],
    *,
    headers: list[str],
    cell_text: Callable[[int, int], str],
    row_for_oid: Callable[[int], int],
) -> bool:
    """True when ``column`` exists and every OID in scope has a valid value."""
    if column not in headers:
        return False
    col_idx = headers.index(column)
    for oid in oids:
        row = row_for_oid(int(oid))
        if row < 0:
            return False
        if not is_valid_descriptor_cell(cell_text(row, col_idx)):
            return False
    return True


def partition_descriptor_jobs(
    disp_headers: list[str],
    int_fns: list,
    output_headers: list[str],
    oids: list[int],
    *,
    headers: list[str],
    cell_text: Callable[[int, int], str],
    row_for_oid: Callable[[int], int],
) -> tuple[list[str], list, list[str], list[str]]:
    """
    Split a Calculate Descriptors selection into work still needed vs columns already filled.

    Returns ``(compute_disp, compute_fns, compute_headers, skipped_column_names)``.
    """
    compute_disp: list[str] = []
    compute_fns: list = []
    compute_hdrs: list[str] = []
    skipped: list[str] = []

    for disp, fn, hdr in zip(disp_headers, int_fns, output_headers):
        existing_name = disp if disp in headers else hdr
        if column_complete_for_oids(
            existing_name,
            oids,
            headers=headers,
            cell_text=cell_text,
            row_for_oid=row_for_oid,
        ):
            skipped.append(existing_name)
            continue
        compute_disp.append(disp)
        compute_fns.append(fn)
        compute_hdrs.append(hdr)

    return compute_disp, compute_fns, compute_hdrs, skipped
