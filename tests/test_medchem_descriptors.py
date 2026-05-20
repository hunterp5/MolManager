"""Tests for Lipinski, InChI key, formula, and CNS MPO-style descriptors."""

from __future__ import annotations

import re

from unittest.mock import patch

import pytest
from rdkit import Chem

from molmanager.medchem_descriptors import (
    cns_mpo_score,
    esol_logS_intrinsic,
    lipinski_violations,
    logd74_value,
    logs74_value,
    mol_formula,
    mol_inchi_key,
    ro5_pass,
)
from molmanager.pkasolver_descriptor_support import int_fns_need_pkasolver
from molmanager.workers.chemistry_tools import descriptor_callable_for_int_fn


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


@patch("molmanager.medchem_descriptors.microstates_for_mol", return_value=None)
def test_cns_mpo_in_range(_mock_ms: object) -> None:
    mol = Chem.MolFromSmiles("c1ccccc1CCN")
    assert mol is not None
    s = cns_mpo_score(mol)
    assert 0.0 <= s <= 6.0


@patch("molmanager.medchem_descriptors.microstates_for_mol", return_value=None)
def test_descriptor_dispatch_custom_ids(_mock_ms: object) -> None:
    cache: dict = {}
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    assert descriptor_callable_for_int_fn("INCHIKEY", cache, None)(mol) == mol_inchi_key(mol)
    assert descriptor_callable_for_int_fn("MOLFORMULA", cache)(mol) == "C2H6O"
    assert descriptor_callable_for_int_fn("RO5_VIOLATIONS", cache)(mol) == 0
    assert descriptor_callable_for_int_fn("RO5_PASS", cache)(mol) == "Yes"
    assert isinstance(descriptor_callable_for_int_fn("CNS_MPO", cache)(mol), float)
    assert isinstance(descriptor_callable_for_int_fn("LOGS_ESOL", cache)(mol), float)


def test_int_fns_need_pkasolver() -> None:
    assert int_fns_need_pkasolver(("MolWt", "LOGD74"))
    assert int_fns_need_pkasolver(("LOGS74",))
    assert int_fns_need_pkasolver(("CNS_MPO",))
    assert not int_fns_need_pkasolver(("QED", "MolWt", "LOGS_ESOL"))


def test_logd74_value_heuristic_when_no_microstates() -> None:
    mol = Chem.MolFromSmiles("[Na+].[Cl-]")
    assert mol is not None
    v = logd74_value(mol, [])
    assert isinstance(v, float)


def test_logs74_value_intrinsic_when_no_microstates() -> None:
    mol = Chem.MolFromSmiles("[Na+].[Cl-]")
    assert mol is not None
    intrinsic = esol_logS_intrinsic(mol)
    assert logs74_value(mol, []) == intrinsic


def test_esol_intrinsic_ethanol() -> None:
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    s = esol_logS_intrinsic(mol)
    assert -2.0 < s < 2.0
