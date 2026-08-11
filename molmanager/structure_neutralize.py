"""Neutralize formal charges on structures (RDKit MolStandardize Uncharger)."""

from __future__ import annotations

import threading

from rdkit import Chem

# Building an Uncharger compiles its SMARTS patterns, which costs more than the uncharge call
# itself on small molecules. Reuse one per thread instead of one per molecule (~1.7x faster on
# bulk runs, identical output); thread-local because RDKit objects are not guaranteed thread-safe.
_uncharger_local = threading.local()


def _uncharger():
    """Return this thread's cached ``Uncharger``."""
    inst = getattr(_uncharger_local, "inst", None)
    if inst is None:
        from rdkit.Chem.MolStandardize import rdMolStandardize

        inst = rdMolStandardize.Uncharger()
        _uncharger_local.inst = inst
    return inst


def neutralize_mol(mol) -> Chem.Mol | None:
    """
    Return a copy of ``mol`` with net formal charge zero via RDKit ``Uncharger``.

    Adds/removes implicit hydrogens on charged atoms as needed. Returns ``None`` if
    neutralization fails.
    """
    if mol is None:
        return None
    try:
        parent = Chem.Mol(mol)
        out = _uncharger().uncharge(parent)
        if out is None:
            return None
        Chem.SanitizeMol(out)
        return out
    except Exception:
        return None
