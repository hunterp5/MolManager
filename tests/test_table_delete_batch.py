"""Bulk delete snapshot helpers (no full main window)."""

from __future__ import annotations

from types import SimpleNamespace

from molmanager.ui.compound_table_model import CompoundTableModel
from molmanager.ui.main_window.table_undo_commands import collect_delete_row_snapshots


def test_collect_delete_row_snapshots_light(qapp):  # noqa: ARG001
    headers = ["ID_HIDDEN", "Structure", "SMILES", "MW"]
    model = CompoundTableModel(headers)
    for oid, smi, mw in ((1, "C", "16"), (2, "CC", "30"), (3, "CCC", "44")):
        model.append_row(oid, {"SMILES": smi, "MW": mw})
    app = SimpleNamespace(
        headers=headers,
        _table_model=model,
        mols={},
    )
    snaps = collect_delete_row_snapshots(app, frozenset({1, 3}), light=True)
    assert len(snaps) == 2
    assert {s.oid for s in snaps} == {1, 3}
    assert all(s.light for s in snaps)
    assert snaps[0].cells["SMILES"] == "C"
    assert snaps[1].cells["MW"] == "44"
