# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

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
