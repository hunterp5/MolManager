"""Neutralize structures (formal charge → 0)."""

from __future__ import annotations

from rdkit import Chem

from molmanager.medchem_descriptors import mol_net_formal_charge
from molmanager.structure_neutralize import neutralize_mol


def test_neutralize_ammonium() -> None:
    mol = Chem.MolFromSmiles("[NH4+]")
    assert mol is not None
    assert mol_net_formal_charge(mol) == 1
    out = neutralize_mol(mol)
    assert out is not None
    assert mol_net_formal_charge(out) == 0


def test_neutralize_carboxylate() -> None:
    mol = Chem.MolFromSmiles("[O-]C(=O)c1ccccc1")
    assert mol is not None
    assert mol_net_formal_charge(mol) == -1
    out = neutralize_mol(mol)
    assert out is not None
    assert mol_net_formal_charge(out) == 0


def test_neutralize_already_neutral() -> None:
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    out = neutralize_mol(mol)
    assert out is not None
    assert mol_net_formal_charge(out) == 0
