"""Tests for ingest cell building (shared by load worker and GUI)."""

from __future__ import annotations

from rdkit import Chem

from molmanager.ingest_cells import apply_structure_field_override, prepare_mol_row, row_cells_from_mol


def test_row_cells_from_mol_smiles_and_props():
    mol = Chem.MolFromSmiles("CCO")
    mol.SetProp("MW", "46.07")
    cells = row_cells_from_mol(mol, ["SMILES", "MW"])
    assert cells["MW"] == "46.07"
    assert "CCO" in cells["SMILES"] or cells["SMILES"]  # canonical


def test_apply_structure_field_override_reparses_smiles_column():
    mol = Chem.MolFromSmiles("C")
    mol.SetProp("alt_smi", "CCO")
    out = apply_structure_field_override(mol, "alt_smi")
    assert out is not None
    assert out.GetNumAtoms() == 3


def test_prepare_mol_row_applies_override_before_cells():
    mol = Chem.MolFromSmiles("C")
    mol.SetProp("alt_smi", "CC")
    mol_out, cells = prepare_mol_row(mol, ["SMILES"], "alt_smi")
    assert mol_out is not None
    assert mol_out.GetNumAtoms() == 2
    assert "SMILES" in cells
