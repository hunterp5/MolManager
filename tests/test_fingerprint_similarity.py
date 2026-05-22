"""Fingerprint similarity helpers and worker."""

from __future__ import annotations

from rdkit import Chem

from molmanager.rdkit_fingerprints import fingerprint_bitvect_for_ui_choice
from molmanager.workers.fingerprint_similarity import pairwise_fingerprint_similarity


def _benzene() -> Chem.Mol:
    return Chem.MolFromSmiles("c1ccccc1")


def _toluene() -> Chem.Mol:
    return Chem.MolFromSmiles("Cc1ccccc1")


def test_pairwise_metrics_for_related_molecules():
    qmol, tmol = _benzene(), _toluene()
    fp_choice = "Morgan (r=2, n=1024)"
    qfp = fingerprint_bitvect_for_ui_choice(qmol, fp_choice)
    fp = fingerprint_bitvect_for_ui_choice(tmol, fp_choice)
    assert qfp is not None and fp is not None
    t = pairwise_fingerprint_similarity(qfp, fp, "Tanimoto")
    d = pairwise_fingerprint_similarity(qfp, fp, "Dice")
    c = pairwise_fingerprint_similarity(qfp, fp, "Cosine")
    assert 0.0 < t < 1.0
    assert 0.0 < d <= 1.0
    assert 0.0 < c <= 1.0


def test_identical_molecules_similarity_one():
    mol = _benzene()
    fp_choice = "Morgan (r=2, n=1024)"
    qfp = fingerprint_bitvect_for_ui_choice(mol, fp_choice)
    assert qfp is not None
    assert pairwise_fingerprint_similarity(qfp, qfp, "Tanimoto") == 1.0
