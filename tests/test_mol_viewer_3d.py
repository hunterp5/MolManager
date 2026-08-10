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
    # View-in-3D chrome: no mouse-controls help or atom info boxes.
    assert "chem3d-help" not in html
    assert "Mouse controls" not in html
    assert "chem-atom-panel" not in html
    assert "chem-atom-detail" not in html


def test_bundled_3dmol_present_in_repo():
    assert bundled_3dmol_available()


def test_prepare_mol_2d_benzene():
    m = Chem.MolFromSmiles("c1ccccc1")
    m2 = prepare_mol_2d(m)
    assert m2 is not None
    assert m2.GetNumConformers() >= 1


def test_prepare_mol_3d_keeps_heteroarene_planar_from_kekule_sketch():
    """Sketch mols are Kekulé + 2D; embed must aromatize so pyridine stays planar."""
    import math

    from rdkit.Chem.rdchem import BondType, Conformer
    from rdkit.Geometry import Point3D

    rw = Chem.RWMol()
    for z in (6, 6, 6, 7, 6, 6):  # C-C-C-N-C-C ring (pyridine kekule order)
        rw.AddAtom(Chem.Atom(z))
    # C1=CC=NC=C1
    bts = (
        BondType.DOUBLE,
        BondType.SINGLE,
        BondType.DOUBLE,
        BondType.SINGLE,
        BondType.DOUBLE,
        BondType.SINGLE,
    )
    for i, bt in enumerate(bts):
        rw.AddBond(i, (i + 1) % 6, bt)
    mol = rw.GetMol()
    conf = Conformer(6)
    for i in range(6):
        ang = 2.0 * math.pi * i / 6.0
        conf.SetAtomPosition(i, Point3D(math.cos(ang), math.sin(ang), 0.0))
    mol.AddConformer(conf, assignId=True)
    assert not any(b.GetIsAromatic() for b in mol.GetBonds())

    m3 = prepare_mol_3d(mol)
    assert m3 is not None
    assert any(b.GetIsAromatic() for b in m3.GetBonds())
    c0 = m3.GetConformer()
    xs = [c0.GetAtomPosition(i).x for i in range(6)]
    ys = [c0.GetAtomPosition(i).y for i in range(6)]
    zs = [c0.GetAtomPosition(i).z for i in range(6)]
    # Plane fit: max |z'| after aligning to best-fit plane should be small.
    import numpy as np

    pts = np.column_stack([xs, ys, zs])
    centered = pts - pts.mean(axis=0)
    _, _, vh = np.linalg.svd(centered)
    dist = centered @ vh[-1]
    assert float(np.max(np.abs(dist))) < 0.05


def test_flat_viewer_html_sets_orthographic():
    m = Chem.MolFromSmiles("C")
    m2 = prepare_mol_2d(m)
    assert m2 is not None
    html = _offline_index_html(_mol_block_b64(m2), flat=True)
    assert "orthographic: true" in html
    assert "const flat = true" in html
