"""Import and construction smoke tests for CI."""

from __future__ import annotations

import molmanager
from molmanager.ui.main_window import ChemicalTableApp


def test_package_version():
    assert hasattr(molmanager, "__version__")
    assert isinstance(molmanager.__version__, str)


def test_user_guides_html_contains_topics():
    from molmanager.ui.user_guides import guide_html

    h = guide_html("pubchem")
    assert "PubChem" in h and "Tanimoto Similarity" in h


def test_pubchem_similarity_results_sort_key():
    from molmanager.ui.external.pubchem import PubChemResult, _pubchem_similarity_sort_key
    from molmanager.ui.strings import COLUMN_TANIMOTO_SIMILARITY

    lo = PubChemResult(1, "C", {COLUMN_TANIMOTO_SIMILARITY: "0.41"})
    hi = PubChemResult(2, "CC", {COLUMN_TANIMOTO_SIMILARITY: "0.92"})
    assert sorted([lo, hi], key=_pubchem_similarity_sort_key, reverse=True)[0] is hi


def test_similarity_fp_type_labels_include_variants():
    from molmanager.rdkit_fingerprints import SIMILARITY_FP_TYPE_LABELS

    joined = "\n".join(SIMILARITY_FP_TYPE_LABELS)
    assert "Atom pair" in joined and "Topological" in joined
    assert "Morgan (r=3" in joined
    assert "FCFP" in joined and "Pattern fingerprint" in joined


def test_fingerprint_bitvect_atom_pair_and_morgan_nbits():
    from rdkit import Chem

    from molmanager.workers.fingerprint_similarity import fingerprint_bitvect_for_ui_choice

    m = Chem.MolFromSmiles("c1ccccc1")
    ap = fingerprint_bitvect_for_ui_choice(m, "Atom pair (hashed, 2048 bits)")
    tt = fingerprint_bitvect_for_ui_choice(m, "Topological torsion (hashed, 2048 bits)")
    assert ap is not None and tt is not None
    assert ap.GetNumBits() == 2048 and tt.GetNumBits() == 2048
    morg = fingerprint_bitvect_for_ui_choice(m, "Morgan (r=2, n=2048)")
    assert morg is not None and morg.GetNumBits() == 2048


def test_parse_molecule_from_cell_text_accepts_smiles_and_inchi():
    from rdkit import Chem

    from molmanager.utils import parse_molecule_from_cell_text

    m1 = parse_molecule_from_cell_text("CCO")
    assert m1 is not None and m1.GetNumAtoms() == 3
    inchi = Chem.MolToInchi(m1)
    m2 = parse_molecule_from_cell_text(inchi)
    assert m2 is not None and m2.GetNumAtoms() == 3


def test_vina_dock_guide_html(qapp):  # noqa: ARG001
    from molmanager.ui.user_guides import guide_html

    h = guide_html("vina_dock")
    assert "Vina" in h and "PDBQT" in h


def test_vina_dock_dialog_constructible(qapp):  # noqa: ARG001
    from molmanager.ui.vina_dock import VinaDockDialog

    d = VinaDockDialog(None)
    assert d.windowTitle()
    d.close()


def test_chemical_table_app_constructible(qapp):  # noqa: ARG001
    w = ChemicalTableApp()
    assert w.windowTitle()
    assert w._table_model is not None
    assert w._sqlite_store is not None


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
    w._rebuild_sqlite_store_from_model()

    w._search_panel.setVisible(True)
    w._populate_table_search_columns_combo()
    note_col = w.headers.index("Note")
    for j in range(w._search_col_combo.count()):
        if w._search_col_combo.itemData(j) == note_col:
            w._search_col_combo.setCurrentIndex(j)
            break
    # Quoted literal matches "ethane" only (not "methane" via partial "eth").
    w._search_partial_cb.setChecked(False)
    w._search_query_edit.setText('"ethane"')
    w._search_substructure_cb.setChecked(False)
    w._run_table_search()
    qapp.processEvents()

    sm = w.table.selectionModel()
    indexes = sm.selectedIndexes()
    ncols = w._table_model.columnCount()
    rows_hit = {ix.row() for ix in indexes}
    assert rows_hit == {0}
    assert len(indexes) == ncols


def test_table_chemistry_context_menu_column_eligibility(qapp):  # noqa: ARG001
    """Chemistry context actions apply to Structure / SMILES-like columns and parseable cells."""
    from rdkit import Chem

    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES", "Note"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "CC", "Note": "ethane"})
    w.mols[0] = Chem.MolFromSmiles("CC")
    w.next_oid = 1

    assert w._column_eligible_for_table_chemistry_menu(0, 0) is False
    assert w._column_eligible_for_table_chemistry_menu(0, 1) is True
    assert w._column_eligible_for_table_chemistry_menu(0, 2) is True
    assert w._column_eligible_for_table_chemistry_menu(0, 3) is False

    w._table_model.set_cell_text(0, "Note", "CCO")
    assert w._column_eligible_for_table_chemistry_menu(0, 3) is True
    mol = w._mol_for_table_context_menu(0, 2)
    assert mol is not None and mol.GetNumAtoms() == 2
    mol_note = w._mol_for_table_context_menu(0, 3)
    assert mol_note is not None and mol_note.GetNumAtoms() == 2


def test_canonical_structure_keys_for_dedup(qapp):  # noqa: ARG001
    from rdkit import Chem

    from molmanager.utils import morgan_tanimoto_to_query

    assert morgan_tanimoto_to_query("CC", "CC") == 1.0
    t = morgan_tanimoto_to_query("CCO", "CC")
    assert t is not None and 0.0 < t < 1.0

    w = ChemicalTableApp()
    w.headers = ["ID_HIDDEN", "Structure", "SMILES"]
    w._table_model.set_headers(list(w.headers))
    w._table_model.append_row(0, {"SMILES": "CC"})
    w.mols[0] = Chem.MolFromSmiles("CC")
    w.next_oid = 1
    k = w.canonical_structure_key_from_smiles("CC")
    assert k
    assert k in w.existing_canonical_structure_keys()
    assert w.canonical_structure_key_from_smiles("C(C)") == k


def test_parse_fasta_records() -> None:
    from molmanager.ui.external.boltz2 import _parse_fasta_records

    text = ">prot1 extra\nAC\n DE\n>sp|Q|x\nLL\n"
    r = _parse_fasta_records(text)
    assert len(r) == 2
    assert r[0][0] == "prot1 extra" and r[0][1] == "ACDE"
    assert r[1][1] == "LL"


def test_data_analysis_outlier_masks() -> None:
    import numpy as np

    from molmanager.ui.data_analysis import (
        _outlier_mask_iqr,
        _outlier_mask_modified_z,
        _outlier_mask_zscore,
    )

    v = np.array([1.0, 2.0, 3.0, 4.0, 1000.0])
    assert _outlier_mask_iqr(v, k=1.5).sum() >= 1
    assert not _outlier_mask_iqr(np.array([1.0, 1.0, 1.0, 1.0]), k=1.5).any()
    vt = np.array([1.0, 2.0, 3.0, 4.0, 50.0])
    assert _outlier_mask_zscore(vt, z=1.5).any()
    assert _outlier_mask_modified_z(vt, threshold=2.0).any()
