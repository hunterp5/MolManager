"""Tests for text-first CSV/SMILES ingest helpers."""

from __future__ import annotations

from molmanager.ingest_text import csv_row_to_cells, is_ingest_cell_batch, smi_line_to_cells


def test_csv_row_to_cells_maps_columns():
    row = {"SMILES": "CCO", "Name": "ethanol", "MW": "46.07"}
    cells = csv_row_to_cells(row, smi_col="SMILES", fieldnames=["SMILES", "Name", "MW"])
    assert cells == {"SMILES": "CCO", "Name": "ethanol", "MW": "46.07"}


def test_csv_row_to_cells_skips_empty_smiles():
    assert csv_row_to_cells({"SMILES": "  "}, smi_col="SMILES", fieldnames=["SMILES"]) is None


def test_smi_line_to_cells():
    assert smi_line_to_cells("c1ccccc1") == {"SMILES": "c1ccccc1"}
    assert smi_line_to_cells("smiles") is None


def test_is_ingest_cell_batch():
    assert is_ingest_cell_batch([{"SMILES": "C"}])
    assert not is_ingest_cell_batch([])

    from rdkit import Chem

    assert not is_ingest_cell_batch([Chem.MolFromSmiles("C")])
