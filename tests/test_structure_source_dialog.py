"""Tests for structure-source column ranking (no Qt event loop)."""

from molmanager.ui.dialogs.structure_source import rank_structure_column_names


def test_rank_structure_column_names_prefers_smiles():
    names = ["MOL_BLOCK", "InChIKey", "SMILES", "canonical_smiles", "notes"]
    ranked = rank_structure_column_names(names)
    assert ranked[0] == "SMILES"
    assert "canonical_smiles" in ranked[:3]
    assert "InChIKey" in ranked
