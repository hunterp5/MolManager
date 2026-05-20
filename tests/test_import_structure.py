"""Tests for import structure-source header detection."""

from chemmanager.import_structure import (
    needs_structure_source_picker,
    structure_source_picker_candidates,
)


def test_inchi_key_not_a_structure_source_candidate():
    headers = ["ID_HIDDEN", "Structure", "Ligand InChI", "Ligand InChI Key", "Target Name"]
    cols = structure_source_picker_candidates(headers)
    assert cols == ["Ligand InChI"]
    assert not needs_structure_source_picker(headers)


def test_needs_picker_when_two_real_structure_columns():
    headers = ["ID_HIDDEN", "Structure", "SMILES", "Ligand InChI"]
    assert needs_structure_source_picker(headers)
