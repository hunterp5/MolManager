"""RDKit helpers for sketch export and atom parsing."""

from __future__ import annotations

from typing import Any

from rdkit import Chem

from .constants import DEFAULT_WILDCARD_ELEMENTS, ELEMENT_UPPER_MAP, WILDCARD_ELEMENT
from .wildcards import (
    _is_wildcard_node,
    _normalize_wildcard_elements,
    _wildcard_query_smarts,
)


def _sanitize_mol_for_smiles(mol: Chem.Mol) -> bool:
    """Try full sanitization; fall back to looser steps so charged / sketch edge cases can export."""
    try:
        Chem.SanitizeMol(mol)
        return True
    except Exception:
        pass
    try:
        mol.UpdatePropertyCache(strict=False)
        Chem.SanitizeMol(mol, sanitizeOps=Chem.SanitizeFlags.SANITIZE_PROPERTIES)
        return True
    except Exception:
        pass
    try:
        mol.UpdatePropertyCache(strict=False)
        return True
    except Exception:
        return False


def _rdkit_atom_from_sketch_node(node: dict[str, Any], formal_charge: int = 0) -> Chem.Atom:
    """
    Build an RDKit atom for SMILES/SMARTS export from a sketcher node.

    Handles wildcards (element-list SMARTS + charge), deuterium/tritium isotopes, and
    normal elements with formal charge.
    """
    fc = int(formal_charge)
    if _is_wildcard_node(node):
        sm = _wildcard_query_smarts(_normalize_wildcard_elements(node), formal_charge=fc)
        try:
            a = Chem.AtomFromSmarts(sm)
        except Exception:
            a = Chem.AtomFromSmarts(
                _wildcard_query_smarts(list(DEFAULT_WILDCARD_ELEMENTS), formal_charge=fc)
            )
        if a is None:
            a = Chem.Atom(0)
            if fc:
                a.SetFormalCharge(fc)
        return a

    el = str(node.get("element") or "C")
    if el == "D":
        a = Chem.Atom(1)
        a.SetIsotope(2)
    elif el == "T":
        a = Chem.Atom(1)
        a.SetIsotope(3)
    else:
        a = Chem.Atom(el)
    if fc:
        a.SetFormalCharge(fc)
    return a


def _sketch_element_from_rdkit_atom(atom: Chem.Atom) -> str:
    """Map an RDKit atom back to a sketcher element symbol (D/T for hydrogen isotopes)."""
    if atom.GetAtomicNum() == 1:
        iso = int(atom.GetIsotope())
        if iso == 2:
            return "D"
        if iso == 3:
            return "T"
        return "H"
    return atom.GetSymbol()


def _parse_atom_symbol_input(raw: str) -> tuple[str, list[str] | None] | None:
    """
    Parse user text from Edit Atom: element symbol or wildcard.
    Returns (element, wildcard_els_or_None) or None if invalid.
    """
    s = (raw or "").strip()
    if not s:
        return None
    low = s.lower()
    if s == "*" or low in ("wildcard", "?", "wild"):
        return (WILDCARD_ELEMENT, None)
    u = s.upper().replace(" ", "")
    sym = ELEMENT_UPPER_MAP.get(u)
    if sym is None:
        if len(u) == 1:
            sym = u
        elif len(u) == 2:
            sym = u[0] + u[1].lower()
        else:
            return None
        try:
            if Chem.GetPeriodicTable().GetAtomicNumber(sym) <= 0:
                return None
        except Exception:
            return None
    return (sym, None)
