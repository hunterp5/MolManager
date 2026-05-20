from __future__ import annotations

from rdkit import Chem

from chemmanager.workers.signals import SubstructureFilterSignals
from chemmanager.workers.substructure_filter import SubstructureFilterWorker


def test_substructure_worker_uses_prebuilt_mol_targets():
    signals = SubstructureFilterSignals()
    out: dict[str, object] = {}
    signals.finished.connect(lambda job_gen, matched: out.update({"gen": job_gen, "matched": matched}))
    worker = SubstructureFilterWorker(
        job_gen=7,
        smarts="CO",
        targets=[(1, Chem.MolFromSmiles("CCO")), (2, Chem.MolFromSmiles("CCN"))],
        signals=signals,
    )
    worker.run()
    assert out["gen"] == 7
    assert out["matched"] == frozenset({1})

