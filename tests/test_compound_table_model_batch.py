from __future__ import annotations

from chemmanager.ui.compound_table_model import CompoundTableModel


def test_append_rows_batch_inserts_all_rows():
    model = CompoundTableModel(["ID_HIDDEN", "Structure", "SMILES", "MW"])
    model.append_rows_batch(
        [
            (10, {"SMILES": "CCO", "MW": "46.1"}),
            (11, {"SMILES": "CCN", "MW": "45.0"}),
            (12, {"SMILES": "CCC", "MW": "44.0"}),
        ]
    )
    assert model.rowCount() == 3
    assert model.row_oid(0) == 10
    assert model.cell_text(2, 2) == "CCC"
    bounds = model.numeric_bounds_by_column()
    assert "MW" in bounds
    assert bounds["MW"]["max"] >= bounds["MW"]["min"]

