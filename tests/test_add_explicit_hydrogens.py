"""Add explicit hydrogens dialog and core helper."""

from __future__ import annotations

import pytest

pytest.importorskip("rdkit")
pytest.importorskip("PyQt5.QtWidgets")

from rdkit import Chem

from molmanager.structure_hydrogens import add_explicit_hydrogens, remove_explicit_hydrogens
from molmanager.ui.dialogs.mol_tools import AddExplicitHydrogensDialog, RemoveExplicitHydrogensDialog


def test_add_explicit_hydrogens_helper_expands_implicit_h():
    mol = Chem.MolFromSmiles("C")
    assert mol is not None
    assert mol.GetNumAtoms() == 1

    out = add_explicit_hydrogens(mol)
    assert out is not None
    assert out.GetNumAtoms() > mol.GetNumAtoms()
    assert any(atom.GetAtomicNum() == 1 for atom in out.GetAtoms())


def test_add_explicit_hydrogens_dialog_defaults(qapp):  # noqa: ARG001
    dlg = AddExplicitHydrogensDialog(["Structure", "SMILES"], 2)
    src, only_sel, no_render = dlg.config()
    assert src == "Structure"
    assert only_sel is False
    assert no_render is False


def test_remove_explicit_hydrogens_helper_strips_explicit_h():
    mol = Chem.MolFromSmiles("C")
    assert mol is not None
    with_h = add_explicit_hydrogens(mol)
    assert with_h is not None
    assert with_h.GetNumAtoms() > mol.GetNumAtoms()

    out = remove_explicit_hydrogens(with_h)
    assert out is not None
    assert out.GetNumAtoms() == mol.GetNumAtoms()
    assert not any(atom.GetAtomicNum() == 1 for atom in out.GetAtoms())


def test_remove_explicit_hydrogens_dialog_defaults(qapp):  # noqa: ARG001
    dlg = RemoveExplicitHydrogensDialog(["Structure", "SMILES"], 2)
    src, only_sel, no_render = dlg.config()
    assert src == "Structure"
    assert only_sel is False
    assert no_render is False
