"""Filter pipeline: numeric range, text, substructure (sync + async), and invalid SMARTS."""

from __future__ import annotations

from rdkit import Chem

from molmanager.ui.main_window import ChemicalTableApp
from molmanager.ui.widgets import CategoryFilterCard, FilterCard, SubstructureFilterCard, TextFilterCard


def _setup_two_row_mw_table(w: ChemicalTableApp) -> FilterCard:
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "MW"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "C", "MW": "10"})
    w._table_model.append_row(1, {"SMILES": "CC", "MW": "50"})
    w.mols[0] = Chem.MolFromSmiles("C")
    w.mols[1] = Chem.MolFromSmiles("CC")
    w.next_oid = 2
    w.calculate_global_bounds()
    card = FilterCard(list(w.global_bounds.keys()), w, initial_property="MW")
    card.restore_state("MW", 5.0, 25.0)
    w.filters = [card]
    return card


def _src_row_visible(w: ChemicalTableApp, source_row: int) -> bool:
    """Proxy-aware visibility check for tests."""
    return w._is_source_row_visible(source_row)


def test_numeric_range_filter_hides_row_outside_bounds(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    _setup_two_row_mw_table(w)
    w._apply_filters_impl_sync(None)
    assert _src_row_visible(w, 0) is True
    assert _src_row_visible(w, 1) is False


def test_text_filter_partial_substring(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "Note"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "CC", "Note": "ethane lot"})
    w._table_model.append_row(1, {"SMILES": "C", "Note": "argon cylinder"})
    w.mols[0] = Chem.MolFromSmiles("CC")
    w.mols[1] = Chem.MolFromSmiles("C")
    w.next_oid = 2
    w.calculate_global_bounds()
    card = TextFilterCard(["SMILES", "Note"], w)
    card.set_column("Note")
    # Partial match on "eth" — present in row 0 only (avoid "methane" which contains "eth" / "ethan").
    card.text_edit.setText("eth")
    w.filters = [card]
    w._apply_filters_impl_sync(None)
    assert _src_row_visible(w, 0) is True
    assert _src_row_visible(w, 1) is False


def test_text_filter_enable_btn_highlighted_when_on(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES"]
    w._table_model.set_headers(list(w.headers))
    w.calculate_global_bounds()
    card = TextFilterCard(["SMILES"], w)
    assert card.filter_enabled() is True
    assert card.enable_btn.property("fcActive") is True


def test_category_filter_enable_btn_highlighted_when_on(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "Phase"]
    w._table_model.set_headers(list(w.headers))
    w.calculate_global_bounds()
    card = CategoryFilterCard(["SMILES", "Phase"], w)
    assert card.filter_enabled() is True
    assert card.enable_btn.property("fcActive") is True


def test_category_filter_defaults_to_all_selected(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "Phase"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "CC", "Phase": "prep"})
    w._table_model.append_row(1, {"SMILES": "C", "Phase": "ship"})
    w.mols[0] = Chem.MolFromSmiles("CC")
    w.mols[1] = Chem.MolFromSmiles("C")
    w.next_oid = 2
    w.calculate_global_bounds()
    card = CategoryFilterCard(["SMILES", "Phase"], w)
    card.set_column("Phase")
    assert card.checked_values() == frozenset({"prep", "ship"})
    w.filters = [card]
    w._apply_filters_impl_sync(None)
    assert _src_row_visible(w, 0) is True
    assert _src_row_visible(w, 1) is True


def test_category_filter_all_button_selects_every_value(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "Phase"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "CC", "Phase": "prep"})
    w._table_model.append_row(1, {"SMILES": "C", "Phase": "ship"})
    w.mols[0] = Chem.MolFromSmiles("CC")
    w.mols[1] = Chem.MolFromSmiles("C")
    w.next_oid = 2
    w.calculate_global_bounds()
    card = CategoryFilterCard(["SMILES", "Phase"], w)
    card.set_column("Phase")
    card._select_all_categories()
    w.filters = [card]
    w._apply_filters_impl_sync(None)
    assert _src_row_visible(w, 0) is True
    assert _src_row_visible(w, 1) is True


def test_category_filter_only_checked_values_visible(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "Phase"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "CC", "Phase": "prep"})
    w._table_model.append_row(1, {"SMILES": "C", "Phase": "ship"})
    w.mols[0] = Chem.MolFromSmiles("CC")
    w.mols[1] = Chem.MolFromSmiles("C")
    w.next_oid = 2
    w.calculate_global_bounds()
    card = CategoryFilterCard(["SMILES", "Phase"], w)
    card.restore_from_session("Phase", ["prep"])
    w.filters = [card]
    w._apply_filters_impl_sync(None)
    assert _src_row_visible(w, 0) is True
    assert _src_row_visible(w, 1) is False


def test_substructure_sync_benzene_smarts_hides_non_aromatic(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "c1ccccc1"})
    w._table_model.append_row(1, {"SMILES": "CC"})
    w.mols[0] = Chem.MolFromSmiles("c1ccccc1")
    w.mols[1] = Chem.MolFromSmiles("CC")
    w.next_oid = 2
    w.calculate_global_bounds()
    card = SubstructureFilterCard()
    card.set_smarts("c1ccccc1")
    w.filters = [card]
    w._apply_filters_impl_sync(None)
    assert _src_row_visible(w, 0) is True
    assert _src_row_visible(w, 1) is False


def test_substructure_invalid_smarts_sets_status(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "CC"})
    w.mols[0] = Chem.MolFromSmiles("CC")
    w.next_oid = 1
    w.calculate_global_bounds()
    card = SubstructureFilterCard()
    card.set_smarts("not_valid_smarts_{{{")
    w.filters = [card]
    w._apply_filters_impl_sync(None)
    assert "invalid" in w.status_label.text().lower()


def test_substructure_async_handoff_hides_rows(qapp, monkeypatch):  # noqa: ARG001
    monkeypatch.setenv("MOLMANAGER_SUBSTRUCTURE_ASYNC_ROWS", "64")
    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES"]
    w._table_model.set_headers(list(w.headers))
    for i in range(70):
        smi = "c1ccccc1" if i == 0 else "CC"
        w._table_model.append_row(i, {"SMILES": smi})
        w.mols[i] = Chem.MolFromSmiles(smi)
    w.next_oid = 70
    w.calculate_global_bounds()
    card = SubstructureFilterCard()
    card.set_smarts("c1ccccc1")
    w.filters = [card]
    w.apply_filters()
    assert w.threadpool.waitForDone(120_000)
    qapp.processEvents()
    assert _src_row_visible(w, 0) is True
    for r in range(1, 70):
        assert _src_row_visible(w, r) is False
