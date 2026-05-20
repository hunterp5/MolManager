"""Header-name helpers for structure-source selection on file import (no Qt)."""

from __future__ import annotations


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def is_inchi_key_header(name: str) -> bool:
    """True for InChIKey-style property names (not full InChI strings)."""
    n = _norm(name)
    if not n:
        return False
    compact = n.replace(" ", "").replace("-", "").replace("_", "")
    if "inchikey" in compact:
        return True
    return "inchi" in n and "key" in n


def header_looks_like_structure_text(name: str) -> bool:
    """Whether a column name may hold parseable structure text (SMILES, InChI, mol block, …)."""
    if name in ("ID_HIDDEN", "Structure", "Fragments"):
        return False
    n = _norm(name)
    if not n:
        return False
    if is_inchi_key_header(name):
        return False
    if "smiles" in n:
        return True
    if "inchi" in n:
        return True
    if n == "smi" or n.endswith("_smi") or n.endswith(".smi"):
        return True
    if n == "structure":
        return True
    if "molblock" in n or "mol_block" in n or "molfile" in n or n.endswith(".mol"):
        return True
    if "v2000" in n or "v3000" in n or "ctab" in n:
        return True
    if "sdf" in n and ("block" in n or "record" in n or n == "sdf"):
        return True
    if "rxn" in n or "reaction" in n:
        return True
    if n == "pdb" or n.endswith("_pdb") or "pdb_block" in n:
        return True
    if "smarts" in n or "smirks" in n:
        return True
    if "csmiles" in n or "cxsmiles" in n or "canonical_smiles" in n or "isomeric_smiles" in n:
        return True
    if "mol_string" in n or n in ("mol", "rmol") or n.endswith("_mol"):
        return True
    return False


def structure_source_picker_candidates(headers: list[str]) -> list[str]:
    """Columns offered when asking which field defines Structure (excludes InChI Keys)."""
    return [h for h in headers[2:] if header_looks_like_structure_text(h)]


def needs_structure_source_picker(headers: list[str]) -> bool:
    return len(structure_source_picker_candidates(headers)) >= 2
