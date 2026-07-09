"""Add explicit hydrogen atoms to RDKit molecules."""

from __future__ import annotations

from rdkit import Chem


def add_explicit_hydrogens(mol: Chem.Mol) -> Chem.Mol | None:
    """
    Return a copy of *mol* with implicit hydrogens expanded to explicit H atoms.

    When a 3D conformer is present, hydrogen coordinates are computed from it.
    """
    if mol is None or mol.GetNumAtoms() == 0:
        return None
    try:
        m = Chem.Mol(mol)
        Chem.SanitizeMol(m)
        if m.GetNumConformers() > 0:
            return Chem.AddHs(m, addCoords=True)
        return Chem.AddHs(m)
    except Exception:
        return None
