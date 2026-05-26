"""Neutralize formal charges on structures (RDKit MolStandardize Uncharger)."""

from __future__ import annotations

from rdkit import Chem


def neutralize_mol(mol) -> Chem.Mol | None:
    """
    Return a copy of ``mol`` with net formal charge zero via RDKit ``Uncharger``.

    Adds/removes implicit hydrogens on charged atoms as needed. Returns ``None`` if
    neutralization fails.
    """
    if mol is None:
        return None
    try:
        from rdkit.Chem.MolStandardize import rdMolStandardize

        parent = Chem.Mol(mol)
        out = rdMolStandardize.Uncharger().uncharge(parent)
        if out is None:
            return None
        Chem.SanitizeMol(out)
        return out
    except Exception:
        return None
