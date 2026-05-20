"""3D viewer helper (RDKit only; no Qt WebEngine required)."""

from __future__ import annotations

from rdkit import Chem

from molmanager.ui.mol_viewer_3d import (
    _mol_block_b64,
    _offline_index_html,
    build_3dmol_html,
    bundled_3dmol_available,
    prepare_mol_2d,
    prepare_mol_3d,
)


def test_prepare_mol_3d_ethanol():
    m = Chem.MolFromSmiles("CCO")
    m3 = prepare_mol_3d(m)
    assert m3 is not None
    assert m3.GetNumConformers() >= 1


def test_build_3dmol_html_contains_script_and_model():
    m = Chem.MolFromSmiles("C")
    m3 = prepare_mol_3d(m)
    assert m3 is not None
    html = build_3dmol_html(_mol_block_b64(m3))
    assert "atob(" in html
    if bundled_3dmol_available():
        assert "3Dmol-min.js" in html
    else:
        assert "3dmol.org" in html
    assert "chem3d-help" in html
    assert "setClickable({}, true, onAtomPick)" in html
    assert "setHoverable({}, true, onAtomHover, onAtomUnhover)" in html
    assert "maybeDeselectBackgroundClick" in html
    assert "chem-atom-detail" in html


def test_bundled_3dmol_present_in_repo():
    assert bundled_3dmol_available()


def test_prepare_mol_2d_benzene():
    m = Chem.MolFromSmiles("c1ccccc1")
    m2 = prepare_mol_2d(m)
    assert m2 is not None
    assert m2.GetNumConformers() >= 1


def test_flat_viewer_html_sets_orthographic():
    m = Chem.MolFromSmiles("C")
    m2 = prepare_mol_2d(m)
    assert m2 is not None
    html = _offline_index_html(_mol_block_b64(m2), flat=True)
    assert "orthographic: true" in html
    assert "const flat = true" in html
