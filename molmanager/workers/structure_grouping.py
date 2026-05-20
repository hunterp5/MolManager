"""Deduplicate table rows by chemical structure for pkasolver-backed tools."""

from __future__ import annotations

from collections import defaultdict

from rdkit import Chem

from ..utils import mol_to_canonical_smiles


def structure_key(mol: Chem.Mol) -> str:
    """Stable key for deduplicating pkasolver work across rows with the same structure."""
    s = mol_to_canonical_smiles(mol)
    if s:
        return s
    try:
        from rdkit.Chem import inchi

        ik = inchi.MolToInchiKey(mol)
    except Exception:
        ik = ""
    return ik or f"__uid_{id(mol)}__"


def group_rows_by_structure(
    rows: list[tuple[int | None, Chem.Mol | None]],
) -> tuple[list[str], dict[str, Chem.Mol], dict[str, list[int | None]]]:
    """
    Collapse rows to unique structures while preserving first-seen key order.

    Returns ``(ordered_keys, key_to_representative_mol, key_to_source_oids)``.
    """
    order: list[str] = []
    rep: dict[str, Chem.Mol] = {}
    oids: dict[str, list[int | None]] = defaultdict(list)
    for oid, mol in rows:
        if mol is None:
            continue
        k = structure_key(mol)
        if k not in rep:
            order.append(k)
            rep[k] = mol
        oids[k].append(oid)
    return order, rep, dict(oids)
