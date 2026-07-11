"""Text-first file ingest helpers (CSV/SMILES lines without RDKit on the load path)."""

from __future__ import annotations


def csv_row_to_cells(
    row: dict[str, str],
    *,
    smi_col: str,
    fieldnames: list[str],
) -> dict[str, str] | None:
    """Build table cell values from one CSV/TSV row; returns None when SMILES is empty."""
    smi = (row.get(smi_col) or "").strip()
    if not smi:
        return None
    cells: dict[str, str] = {"SMILES": smi}
    for h in fieldnames:
        if h == smi_col:
            continue
        cells[h] = str(row.get(h, "") or "")
    return cells


def smi_line_to_cells(line: str) -> dict[str, str] | None:
    """Build cells for a one-SMILES-per-line text file."""
    smi = (line or "").strip()
    if not smi or smi.lower().startswith("smiles"):
        return None
    return {"SMILES": smi}


def is_ingest_cell_batch(batch: list) -> bool:
    """True when a worker batch carries pre-built cell dicts instead of RDKit mols."""
    return bool(batch) and isinstance(batch[0], dict)
