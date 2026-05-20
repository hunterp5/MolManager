"""Tests for BRICS / RECAP fragment decomposition helpers."""

from rdkit import Chem

from chemmanager.fragment_decomposition import (
    assemble_fragment_table_rows,
    decompose_brics,
    decompose_recap,
    decompose_fragments,
    detect_fragment_column_prefixes,
    fragment_columns_for_prefix,
    recompose_fragments,
)


def test_decompose_brics_aspirin():
    mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")
    frags = decompose_brics(mol)
    assert len(frags) >= 2
    assert all(isinstance(s, str) for s in frags)


def test_decompose_recap_aspirin():
    mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")
    frags = decompose_recap(mol)
    assert len(frags) >= 2


def test_decompose_recap_benzene_falls_back_to_root():
    mol = Chem.MolFromSmiles("c1ccccc1")
    assert decompose_recap(mol) == ["c1ccccc1"]


def test_assemble_fragment_table_rows_padding():
    rows, headers = assemble_fragment_table_rows(
        [1, 2],
        [["a", "b"], ["x"]],
        "BRICS",
    )
    assert headers == ["BRICS_1", "BRICS_2"]
    assert rows[0] == (1, {"BRICS_1": "a", "BRICS_2": "b"})
    assert rows[1] == (2, {"BRICS_1": "x", "BRICS_2": "N/A"})


def test_decompose_fragments_dispatch():
    mol = Chem.MolFromSmiles("CC")
    assert decompose_fragments(mol, "brics") == decompose_brics(mol)
    assert decompose_fragments(mol, "recap") == decompose_recap(mol)


def test_detect_fragment_column_prefixes():
    headers = ["ID_HIDDEN", "Structure", "BRICS_1", "BRICS_2", "RECAP_1", "MolWt"]
    assert detect_fragment_column_prefixes(headers) == ["BRICS", "RECAP"]
    assert fragment_columns_for_prefix(headers, "BRICS") == ["BRICS_1", "BRICS_2"]


def test_recompose_brics_from_aspirin_fragments():
    mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")
    frags = decompose_brics(mol)
    products = recompose_fragments(frags, "brics", max_depth=2, max_products=50)
    assert len(products) >= 2
    assert any("c1ccccc1" in s for s in products)
