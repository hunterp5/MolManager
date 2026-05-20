"""RDKit helpers for sketch export and atom parsing."""

from rdkit import Chem

from .constants import ELEMENT_UPPER_MAP, WILDCARD_ELEMENT


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
