"""Chunked SQLite row export from CompoundTableModel."""

from __future__ import annotations

from molmanager.ui.compound_table_model import CompoundTableModel


def test_export_rows_for_sqlite_slice() -> None:
    model = CompoundTableModel(["ID_HIDDEN", "Structure", "SMILES", "MW"])
    model.append_rows_batch(
        [
            (0, {"SMILES": "CCO", "MW": "46"}),
            (1, {"SMILES": "CC", "MW": "30"}),
            (2, {"SMILES": "C", "MW": "16"}),
        ]
    )
    part = model.export_rows_for_sqlite_slice(["SMILES", "MW"], 1, 3)
    assert part == [(1, {"SMILES": "CC", "MW": "30"}), (2, {"SMILES": "C", "MW": "16"})]
    full = model.export_rows_for_sqlite(["SMILES"])
    assert len(full) == 3
