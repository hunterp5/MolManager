"""Sanity checks for citation helper strings."""

from __future__ import annotations

from molmanager import science_citations as sc


def test_plain_citations_contain_dois() -> None:
    assert "10.3389/fchem.2022.866585" in sc.PKASOLVER
    assert "10.1186/s13321-019-0336-9" in sc.DIMORPHITE_DL
    assert "10.1021/ci034243x" in sc.ESOL_DELANEY
    assert "10.1021/cn100008c" in sc.WAGER_CNS_MPO


def test_descriptor_footer_html_links() -> None:
    html = sc.descriptor_dialog_footer_html()
    assert "doi.org" in html
    assert "Mayr" in html


def test_descriptor_checkbox_citations() -> None:
    assert sc.descriptor_checkbox_citation_html("LOGD74") is not None
    assert "Mayr" in sc.descriptor_checkbox_citation_html("LOGD74") or ""
    assert sc.descriptor_checkbox_citation_html("FP_Pharm2D_Gobbi") is not None
    assert sc.descriptor_checkbox_citation_html("AB_MPS") is not None
    assert "Shultz" in (sc.descriptor_checkbox_citation_html("AB_MPS") or "")
    assert sc.descriptor_checkbox_citation_html("MolWt") is None


def test_ab_mps_score_formula() -> None:
    from rdkit import Chem

    from molmanager.medchem_descriptors import ab_mps_score

    mol = Chem.MolFromSmiles("c1ccccc1")
    score = ab_mps_score(mol)
    assert score >= 0.0
    assert score == abs(score)  # non-negative sum of positive terms


def test_surechembl_patent_html() -> None:
    html = sc.surechembl_patent_search_html()
    assert "SureChEMBL" in html
    assert "surechembl" in html.lower()
