"""Session document build/apply round-trip (requires Qt + main window)."""

from __future__ import annotations

import json

import pytest
from rdkit import Chem

from chemmanager.ui.main_window import ChemicalTableApp


def test_session_document_json_roundtrip_preserves_keys(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "Note"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "CC", "Note": "ethane"})
    w.mols[0] = Chem.MolFromSmiles("CC")
    w.next_oid = 1

    doc = w._build_session_document()
    wire = json.dumps(doc)
    doc2 = json.loads(wire)

    assert doc2["format"] == doc["format"]
    assert doc2["version"] == doc["version"]
    assert doc2["headers"] == w.headers
    assert len(doc2["rows"]) == 1
    assert doc2["rows"][0]["id"] == 0
    assert "CC" in (doc2["rows"][0]["cells"].get("SMILES") or "")


def test_apply_session_document_restores_row(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "Note"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "CC", "Note": "ethane"})
    w.mols[0] = Chem.MolFromSmiles("CC")
    w.next_oid = 1

    doc = w._build_session_document()

    w2 = ChemicalTableApp()
    w2._apply_session_document(doc)

    assert w2.headers[:4] == ["ID_HIDDEN", "Structure", "SMILES", "Note"]
    assert w2._table_model.rowCount() == 1
    smi_col = w2.headers.index("SMILES")
    assert "CC" in (w2._table_model.cell_text(0, smi_col) or "")
    assert 0 in w2.mols
