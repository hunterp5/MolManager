"""RDKit Leader sphere-exclusion clustering."""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import AllChem

from molmanager.workers.cluster_worker import cluster_sphere_exclusion


def test_sphere_exclusion_assigns_nearest_centroid():
    smis = ["CCO", "CCCO", "c1ccccc1", "Cc1ccccc1", "CC(=O)O"]
    fps = [
        AllChem.GetMorganFingerprintAsBitVect(Chem.MolFromSmiles(s), 2, 2048)
        for s in smis
    ]
    labels = cluster_sphere_exclusion(fps, 0.25)
    assert labels is not None
    assert labels.shape[0] == 5
    assert len(set(int(x) for x in labels)) >= 2


def test_sphere_exclusion_high_cutoff_yields_many_clusters():
    smis = ["CCO", "CCC", "CCCC", "CCCCC"]
    fps = [
        AllChem.GetMorganFingerprintAsBitVect(Chem.MolFromSmiles(s), 2, 2048)
        for s in smis
    ]
    labels = cluster_sphere_exclusion(fps, 0.05)
    assert labels is not None
    assert len(set(int(x) for x in labels)) == 4
