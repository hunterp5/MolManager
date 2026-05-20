from __future__ import annotations

from molmanager.ui.filters.cards import TextFilterCard
from molmanager.ui.main_window import ChemicalTableApp


def test_proxy_filter_mode_reduces_visible_rows(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "Note"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_rows_batch(
        [
            (0, {"SMILES": "CCO", "Note": "alpha"}),
            (1, {"SMILES": "CCN", "Note": "beta"}),
            (2, {"SMILES": "CCC", "Note": "alpha"}),
        ]
    )
    card = TextFilterCard(["SMILES", "Note"], w)
    card.set_column("Note")
    card.text_edit.setText("alpha")
    w.filters = [card]
    w._apply_filters_impl_sync(None)
    proxy = w._filter_proxy_model
    assert proxy is not None
    assert proxy.rowCount() == 2
    w.close()

