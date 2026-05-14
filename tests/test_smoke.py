"""Import and construction smoke tests for CI."""

from __future__ import annotations

import chemmanager
from chemmanager.ui.main_window import ChemicalTableApp


def test_package_version():
    assert hasattr(chemmanager, "__version__")
    assert isinstance(chemmanager.__version__, str)


def test_parse_molecule_from_cell_text_accepts_smiles_and_inchi():
    from rdkit import Chem

    from chemmanager.utils import parse_molecule_from_cell_text

    m1 = parse_molecule_from_cell_text("CCO")
    assert m1 is not None and m1.GetNumAtoms() == 3
    inchi = Chem.MolToInchi(m1)
    m2 = parse_molecule_from_cell_text(inchi)
    assert m2 is not None and m2.GetNumAtoms() == 3


def test_chemical_table_app_constructible(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    assert w.windowTitle()
    assert w._table_model is not None


def test_app_table_search_selects_matching_row(qapp):  # noqa: ARG001
    """Integration: table + search bar selects every cell in rows whose column matches the query."""
    from rdkit import Chem

    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "Note"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "CC", "Note": "ethane"})
    w._table_model.append_row(1, {"SMILES": "C", "Note": "methane"})
    w.mols[0] = Chem.MolFromSmiles("CC")
    w.mols[1] = Chem.MolFromSmiles("C")
    w.next_oid = 2
    w.calculate_global_bounds()

    w._search_panel.setVisible(True)
    w._populate_table_search_columns_combo()
    note_col = w.headers.index("Note")
    for j in range(w._search_col_combo.count()):
        if w._search_col_combo.itemData(j) == note_col:
            w._search_col_combo.setCurrentIndex(j)
            break
    # Partial "eth"/"etha" still hits "methane"; match the first row only via full cell text.
    w._search_partial_cb.setChecked(False)
    w._search_query_edit.setText("ethane")
    w._search_substructure_cb.setChecked(False)
    w._run_table_search()
    qapp.processEvents()

    sm = w.table.selectionModel()
    indexes = sm.selectedIndexes()
    ncols = w._table_model.columnCount()
    rows_hit = {ix.row() for ix in indexes}
    assert rows_hit == {0}
    assert len(indexes) == ncols
