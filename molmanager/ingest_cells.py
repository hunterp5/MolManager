"""Build table cell text from RDKit molecules during file ingest (worker or GUI thread)."""

from __future__ import annotations

from rdkit import Chem

from .utils import mol_to_canonical_smiles, parse_molecule_from_cell_text, safe_mol_prop_string


def apply_structure_field_override(mol: Chem.Mol | None, field: str | None) -> Chem.Mol | None:
    """When the user picked a non-default structure column, re-parse chemistry from that field."""
    if not field or mol is None:
        return mol
    if not mol.HasProp(field):
        return mol
    raw = (safe_mol_prop_string(mol, field) or "").strip()
    if not raw:
        return mol
    parsed = parse_molecule_from_cell_text(raw)
    return parsed if parsed is not None else mol


def row_cells_from_mol(mol: Chem.Mol | None, data_headers: list[str]) -> dict[str, str]:
    """Build ``{header: text}`` for one molecule (data columns only, not ID/Structure)."""
    values: dict[str, str] = {}
    if mol is None:
        return {name: "" for name in data_headers}
    for name in data_headers:
        if name == "SMILES":
            if mol.HasProp("SMILES"):
                txt = (safe_mol_prop_string(mol, "SMILES") or "").strip()
            else:
                try:
                    txt = mol_to_canonical_smiles(mol)
                except Exception:
                    txt = ""
        else:
            txt = safe_mol_prop_string(mol, name)
        values[name] = txt
    return values


def prepare_mol_row(mol: Chem.Mol, data_headers: list[str], structure_field: str | None) -> tuple[Chem.Mol, dict[str, str]]:
    """Apply structure override and return ``(mol_for_store, cells)`` for one ingest row."""
    mol_out = apply_structure_field_override(mol, structure_field)
    return mol_out, row_cells_from_mol(mol_out, data_headers)
