"""Diverse subset (MaxMin) picking."""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import AllChem

from molmanager.fingerprint_cache import clear as clear_fp_cache
from molmanager.fingerprint_cache import get as cache_get
from molmanager.fingerprint_cache import store_from_mol
from molmanager.workers.diverse_subset_worker import (
    DiverseSubsetPoolRow,
    DiverseSubsetWorker,
    build_diverse_subset_pool,
    maxmin_diverse_pick_bulk,
    maxmin_diverse_pick_indices,
    maxmin_diverse_pick_lazy,
)
from molmanager.workers.signals import DiverseSubsetSignals


def _morgan_fps(smis: list[str]):
    mols = [Chem.MolFromSmiles(s) for s in smis]
    assert all(m is not None for m in mols)
    return [AllChem.GetMorganFingerprintAsBitVect(m, 2, 2048) for m in mols]


def test_maxmin_pick_size_and_spread():
    smis = ["CCO", "CCCO", "c1ccccc1", "Cc1ccccc1", "CC(=O)O", "CCC"]
    fps = _morgan_fps(smis)
    picks = maxmin_diverse_pick_indices(fps, 3, seed=42)
    assert len(picks) == 3
    assert len(set(picks)) == 3
    assert not ({2, 3} <= set(picks))


def test_maxmin_returns_all_when_k_ge_n():
    fps = _morgan_fps(["CCO", "CCC", "CCCC"])
    picks = maxmin_diverse_pick_indices(fps, 10)
    assert picks == [0, 1, 2]


def test_bulk_pick_matches_lazy_on_small_pool():
    smis = ["CCO", "CCCO", "c1ccccc1", "Cc1ccccc1", "CC(=O)O", "CCC"]
    fps = _morgan_fps(smis)
    bulk = maxmin_diverse_pick_bulk(fps, 3, seed=42)
    lazy = maxmin_diverse_pick_indices(fps, 3, seed=42)
    assert len(bulk) == len(lazy) == 3
    assert len(set(bulk)) == 3


def test_lazy_pick_uses_cached_fp_without_mol():
    smis = ["CCO", "c1ccccc1", "CC(=O)O", "CCC"]
    mols = [Chem.MolFromSmiles(s) for s in smis]
    fps = _morgan_fps(smis)
    pool = [
        DiverseSubsetPoolRow(oid=i, mol=mols[i] if i == 0 else None, fp=fps[i])
        for i in range(4)
    ]
    picks = maxmin_diverse_pick_lazy(pool, "Morgan (r=2, n=2048)", 2, seed=1)
    assert len(picks) == 2


def test_build_pool_uses_onbits_column_filter():
    clear_fp_cache()
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    store_from_mol(1, "FP_Morgan_2_2048", mol)
    rows = [(1, mol), (2, mol)]
    pool_all, _ = build_diverse_subset_pool(rows, "Morgan (r=2, n=2048)")
    assert len(pool_all) == 2
    assert cache_get(1, "FP_Morgan_2_2048") is not None
    pool_filt, _ = build_diverse_subset_pool(
        rows,
        "Morgan (r=2, n=2048)",
        onbits_by_oid={1: "12", 2: "N/A"},
        require_onbits_column=True,
    )
    assert len(pool_filt) == 1
    assert pool_filt[0].fp is not None


class _CaptureSignals(DiverseSubsetSignals):
    def __init__(self) -> None:
        super().__init__(None)
        self.picked: list[int] = []
        self.rows: list = []
        self.n_cached = 0
        self.n_computed = 0
        self.err: str | None = None
        self.finished.connect(self._on_done)
        self.failed.connect(self._on_fail)

    def _on_done(self, picked, rows, n_cached, n_computed) -> None:
        self.picked = list(picked)
        self.rows = list(rows)
        self.n_cached = int(n_cached)
        self.n_computed = int(n_computed)

    def _on_fail(self, msg: str) -> None:
        self.err = msg


def test_diverse_subset_worker_picks_k():
    smis = ["CCO", "CCCO", "c1ccccc1", "CC(=O)O", "CCC", "C=C"]
    rows = []
    for i, s in enumerate(smis):
        m = Chem.MolFromSmiles(s)
        assert m is not None
        rows.append((i, m))
    sig = _CaptureSignals()
    worker = DiverseSubsetWorker(rows, "Morgan (r=2, n=2048)", 4, sig)
    worker.run()
    assert sig.err is None
    assert len(sig.picked) == 4
    assert len(sig.rows) == 4
    ranks = {oid: rank for oid, rank in sig.rows}
    assert set(ranks.values()) == {"1", "2", "3", "4"}
