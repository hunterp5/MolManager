"""Tests for silent bulk row append on CompoundTableModel."""

from __future__ import annotations

import pytest

from molmanager.ui.compound_table_model import CompoundTableModel


@pytest.fixture()
def model(qapp):  # noqa: ARG001
    m = CompoundTableModel(["ID_HIDDEN", "Structure", "SMILES", "MW"])
    return m


def test_silent_append_emits_once(model: CompoundTableModel):
    model.begin_silent_appends()
    model.append_rows_batch([(1, {"SMILES": "C", "MW": "16"})], defer_color_cache=True)
    model.append_rows_batch([(2, {"SMILES": "CC", "MW": "30"})], defer_color_cache=True)
    assert model.rowCount() == 2
    model.end_silent_appends()
    assert model.rowCount() == 2
    assert model.value_for_header(0, "SMILES") == "C"
    assert model.value_for_header(1, "SMILES") == "CC"
