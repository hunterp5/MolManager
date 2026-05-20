"""Sanity checks for citation helper strings."""

from __future__ import annotations

from molmanager import science_citations as sc


def test_plain_citations_contain_dois() -> None:
    assert "10.3389/fchem.2022.866585" in sc.PKASOLVER
    assert "10.1186/s13321-019-0336-9" in sc.DIMORPHITE_DL
    assert "10.1021/ci034243r" in sc.ESOL_DELANEY
    assert "10.1021/cn100008c" in sc.WAGER_CNS_MPO


def test_descriptor_footer_html_links() -> None:
    html = sc.descriptor_dialog_footer_html()
    assert "doi.org" in html
    assert "Mayr" in html


def test_surechembl_patent_html() -> None:
    html = sc.surechembl_patent_search_html()
    assert "SureChEMBL" in html
    assert "surechembl" in html.lower()
