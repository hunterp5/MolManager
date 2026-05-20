"""SubstructureFilterWorker (background substructure match)."""

from __future__ import annotations

from PyQt5.QtCore import QThreadPool

from chemmanager.workers import SubstructureFilterSignals, SubstructureFilterWorker


def test_substructure_worker_ethane_matches_c(qapp):  # noqa: ARG001
    sig = SubstructureFilterSignals()
    results: list[tuple[int, frozenset]] = []

    def _on_finished(gen, matched):
        results.append((gen, matched))

    sig.finished.connect(_on_finished)
    pool = QThreadPool()
    targets = [(0, "CC"), (1, "c1ccccc1")]
    pool.start(SubstructureFilterWorker(7, "C", targets, sig))
    assert pool.waitForDone(60000)
    qapp.processEvents()
    assert len(results) == 1
    gen, matched = results[0]
    assert gen == 7
    assert 0 in matched
    assert 1 not in matched


def test_substructure_worker_invalid_smarts_empty_set(qapp):  # noqa: ARG001
    sig = SubstructureFilterSignals()
    results: list[frozenset] = []
    sig.finished.connect(lambda _g, m: results.append(m))
    pool = QThreadPool()
    pool.start(SubstructureFilterWorker(1, "not_valid_smarts_{{{", [(0, "CC")], sig))
    assert pool.waitForDone(60000)
    qapp.processEvents()
    assert results and results[0] == frozenset()
