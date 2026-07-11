"""Tests for BRICS / RECAP recomposition output filters."""

from rdkit import Chem

from molmanager.fragment_decomposition import decompose_brics, recompose_fragments
from molmanager.fragment_recomposition_filters import (
    filter_product_smiles,
    parse_recomposition_filter_text,
    product_passes_filters,
)


def test_parse_recomposition_filter_text_range_and_comparisons():
    rules = parse_recomposition_filter_text("MW 200-500, LogP <= 5, HeavyAtoms >= 10")
    assert len(rules) == 3
    assert rules[0].property_key == "MolWt"
    assert rules[0].op == "range"
    assert rules[1].property_key == "MolLogP"
    assert rules[1].op == "lte"
    assert rules[2].property_key == "HeavyAtomCount"
    assert rules[2].op == "gte"


def test_filter_product_smiles_by_molecular_weight():
    mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")
    frags = decompose_brics(mol)
    products = recompose_fragments(frags, "brics", max_depth=2, max_products=50)
    assert products

    kept, filtered = filter_product_smiles(products, "MW <= 500")
    assert kept
    assert filtered >= 0
    assert len(kept) + filtered == len(products)

    for smi in kept:
        m = Chem.MolFromSmiles(smi)
        assert m is not None
        assert product_passes_filters(m, parse_recomposition_filter_text("MW <= 500"))


def test_filter_product_smiles_rejects_all_when_impossible():
    mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")
    frags = decompose_brics(mol)
    products = recompose_fragments(frags, "brics", max_depth=2, max_products=50)
    kept, filtered = filter_product_smiles(products, "MW < 1")
    assert kept == []
    assert filtered == len(products)
