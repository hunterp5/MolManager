"""Tests for CompoundTableModel (no full window required)."""

from __future__ import annotations

import pytest

from chemmanager.ui.compound_table_model import CompoundTableModel


@pytest.fixture()
def model(qapp):  # noqa: ARG001 — ensures QApplication exists for Qt types
    headers = ["ID_HIDDEN", "Structure", "SMILES", "MW"]
    m = CompoundTableModel(headers)
    return m


def test_append_row_and_oid_index(model: CompoundTableModel):
    model.append_row(10, {"SMILES": "CC", "MW": "30.07"})
    assert model.rowCount() == 1
    assert model.row_oid(0) == 10
    assert model.logical_row_for_oid(10) == 0
    assert (model.cell_text(0, model._headers.index("SMILES")) or "") == "CC"


def test_set_cell_text_updates_value(model: CompoundTableModel):
    model.append_row(1, {"SMILES": "C", "MW": "16"})
    model.set_cell_text(1, "MW", "16.043")
    sci = model._headers.index("SMILES")
    mwi = model._headers.index("MW")
    assert model.cell_text(0, mwi) == "16.043"
    assert model.cell_text(0, sci) == "C"


def test_set_cell_text_batch_updates_row(model: CompoundTableModel):
    model.append_row(7, {"SMILES": "CC", "MW": "30"})
    model.set_cell_text_batch(7, {"SMILES": "CCC", "MW": "44.1"})
    sci = model._headers.index("SMILES")
    mwi = model._headers.index("MW")
    assert model.cell_text(0, sci) == "CCC"
    assert model.cell_text(0, mwi) == "44.1"


def test_numeric_bounds_by_column(model: CompoundTableModel):
    model.append_row(0, {"SMILES": "C", "MW": "10"})
    model.append_row(1, {"SMILES": "CC", "MW": "20"})
    bounds = model.numeric_bounds_by_column()
    assert "MW" in bounds
    assert bounds["MW"]["min"] == 10.0
    assert bounds["MW"]["max"] == 20.0
    assert bounds["MW"]["is_int"] is True


def test_numeric_bounds_mixed_int_and_float_not_integer_column(model: CompoundTableModel):
    model.append_row(0, {"SMILES": "C", "MW": "10"})
    model.append_row(1, {"SMILES": "CC", "MW": "20.5"})
    bounds = model.numeric_bounds_by_column()
    assert bounds["MW"]["min"] == 10.0
    assert bounds["MW"]["max"] == 20.5
    assert bounds["MW"]["is_int"] is False


def test_numeric_bounds_incremental_edit_matches_full_rescan(model: CompoundTableModel):
    model.append_row(0, {"SMILES": "C", "MW": "10", "LogP": "1"})
    model.append_row(1, {"SMILES": "CC", "MW": "20", "LogP": "2"})
    model.numeric_bounds_by_column()
    model.set_cell_text(0, "MW", "15")
    b1 = model.numeric_bounds_by_column()
    model._invalidate_numeric_bounds_all()
    b2 = model.numeric_bounds_by_column()
    assert b1 == b2
    assert b1["MW"] == {"min": 15.0, "max": 20.0, "is_int": True}


def test_set_column_text_by_oids(model: CompoundTableModel):
    model.append_row(10, {"SMILES": "C", "MW": "1"})
    model.append_row(11, {"SMILES": "CC", "MW": "2"})
    model.set_column_text_by_oids("MW", [(10, "3"), (11, "4")])
    mwi = model._headers.index("MW")
    assert model.cell_text(0, mwi) == "3"
    assert model.cell_text(1, mwi) == "4"
