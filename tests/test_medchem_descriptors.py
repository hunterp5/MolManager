"""Tests for Lipinski, InChI key, formula, and CNS MPO-style descriptors."""

from __future__ import annotations

import re

from rdkit import Chem

from chemmanager.medchem_descriptors import (
    cns_mpo_score,
    lipinski_violations,
    mol_formula,
    mol_inchi_key,
    ro5_pass,
)
from chemmanager.workers.chemistry_tools import descriptor_callable_for_int_fn


def test_lipinski_ethanol_zero_violations() -> None:
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    assert lipinski_violations(mol) == 0
    assert ro5_pass(mol) == "Yes"


def test_ro5_fail_high_mw() -> None:
    mol = Chem.MolFromSmiles("C" * 120)
    assert mol is not None
    assert lipinski_violations(mol) >= 1
    assert ro5_pass(mol) == "No"


def test_inchi_key_and_formula_ethanol() -> None:
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    key = mol_inchi_key(mol)
    assert re.match(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$", key)
    assert mol_formula(mol) == "C2H6O"


def test_cns_mpo_in_range() -> None:
    mol = Chem.MolFromSmiles("c1ccccc1CCN")
    assert mol is not None
    s = cns_mpo_score(mol)
    assert 0.0 <= s <= 6.0


def test_descriptor_dispatch_custom_ids() -> None:
    cache: dict = {}
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    assert descriptor_callable_for_int_fn("INCHIKEY", cache)(mol) == mol_inchi_key(mol)
    assert descriptor_callable_for_int_fn("MOLFORMULA", cache)(mol) == "C2H6O"
    assert descriptor_callable_for_int_fn("RO5_VIOLATIONS", cache)(mol) == 0
    assert descriptor_callable_for_int_fn("RO5_PASS", cache)(mol) == "Yes"
    assert isinstance(descriptor_callable_for_int_fn("CNS_MPO", cache)(mol), float)
