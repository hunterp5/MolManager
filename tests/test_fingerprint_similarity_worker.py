"""Fingerprint similarity worker progress and Gobbi process path."""

from __future__ import annotations

import threading

from rdkit import Chem

from molmanager.tool_progress import ToolProgressState
from molmanager.workers.fingerprint_similarity import FPSimilarityWorker
from molmanager.workers.signals import FPSimilaritySignals


class _CaptureSignals(FPSimilaritySignals):
    def __init__(self) -> None:
        super().__init__(None)
        self.rows: list = []
        self.err: str | None = None
        self.finished.connect(self._on_done)
        self.failed.connect(self._on_fail)

    def _on_done(self, rows) -> None:
        self.rows = list(rows or [])

    def _on_fail(self, msg: str) -> None:
        self.err = msg


def test_gobbi_similarity_updates_progress_state():
    mol = Chem.MolFromSmiles("c1ccccc1")
    mol2 = Chem.MolFromSmiles("Cc1ccccc1")
    assert mol is not None and mol2 is not None
    state = ToolProgressState()
    state.begin("Fingerprint similarity", 3)
    sig = _CaptureSignals()
    cancel = threading.Event()
    worker = FPSimilarityWorker(
        mol,
        [(1, mol), (2, mol2)],
        "2D pharmacophore (Gobbi)",
        sig,
        metric="Tanimoto",
        cancel_event=cancel,
        progress_state=state,
    )
    worker.run()
    assert sig.err is None
    assert len(sig.rows) == 2
    msg, done, total, active = state.snapshot()
    assert done == total == 3
    assert "Fingerprint similarity" in msg
