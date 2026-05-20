"""pKa / pkasolver helpers (no pkasolver import required for prepare_mol)."""

from __future__ import annotations

import sys
import types

import pytest
from rdkit import Chem

import molmanager.workers.pka_predictor as pka_predictor
import molmanager.workers.pkasolver_parallel as pkasolver_parallel
from molmanager.workers.pka_predictor import PKaPredictorSignals, PKaPredictorWorker, prepare_mol_for_pkasolver
from molmanager.workers.signals import WorkerSignals


@pytest.fixture(autouse=True)
def _force_sequential_pka_worker(monkeypatch) -> None:
    """Keep pKa worker tests on the in-process path (mocked pkasolver), not a process pool."""
    monkeypatch.setenv("MOLMANAGER_PKA_PROCESS_WORKERS", "1")
    monkeypatch.setattr(
        pkasolver_parallel,
        "plan_pkasolver_process_workers",
        lambda _n, _c: (False, 1),
    )
    monkeypatch.setattr(pka_predictor, "_query_model_singleton", None)


def test_prepare_mol_for_pkasolver_strips_non_utf8_sdf_prop() -> None:
    m = Chem.MolFromSmiles("CCO")
    assert m is not None
    # Simulate vendor SDF metadata stored as Latin-1 bytes (0xa6 is common in old files).
    m.SetProp("_test_bad", b"\xa6vendor".decode("latin-1"))
    safe = prepare_mol_for_pkasolver(m)
    assert safe is not None
    assert safe.GetNumAtoms() == m.GetNumAtoms()
    if hasattr(safe, "GetPropsAsDict"):
        safe.GetPropsAsDict()


def test_pka_worker_emits_partial_results_on_cancel(monkeypatch) -> None:
    class _CancelAfterFirst:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def trigger(self) -> None:
            self._flag = True

    class _State:
        def __init__(self, pka: float) -> None:
            self.pka = pka

    cancel = _CancelAfterFirst()
    fake_mod = types.ModuleType("pkasolver.query")

    class _QueryModel:
        pass

    def _calc(_mol, *, query_model=None):
        _ = query_model
        cancel.trigger()
        return [_State(7.1), _State(4.2)]

    fake_mod.QueryModel = _QueryModel
    fake_mod.calculate_microstate_pka_values = _calc
    monkeypatch.setitem(sys.modules, "pkasolver.query", fake_mod)
    monkeypatch.setattr("molmanager.workers.pka_predictor._patch_pkasolver_dimorphite", lambda: None)
    monkeypatch.setattr("molmanager.workers.pka_predictor._ensure_cairosvg_importable", lambda: None)
    monkeypatch.setattr("molmanager.workers.pka_predictor._query_model_singleton", None)
    ws = WorkerSignals()
    ps = PKaPredictorSignals()
    partial: list[tuple[str, int, int]] = []
    finished: list[list[tuple[int | None, str]]] = []
    ws.partial_results.connect(lambda tool, done, total: partial.append((tool, done, total)))
    ps.finished.connect(lambda rows: finished.append(rows))

    rows = [(1, Chem.MolFromSmiles("CCO")), (2, Chem.MolFromSmiles("CCN"))]
    worker = PKaPredictorWorker(rows, ws, ps, cancel_event=cancel)
    worker.run()

    assert finished, "expected finished signal"
    assert finished[0], "expected at least one completed pKa row"
    assert partial == [("pKa prediction", 1, 2)]


def test_pka_worker_deduplicates_identical_structures(monkeypatch) -> None:
    call_count = 0

    class _State:
        def __init__(self, pka: float) -> None:
            self.pka = pka

    class _CancelNever:
        def is_set(self) -> bool:
            return False

    fake_mod = types.ModuleType("pkasolver.query")

    class _QueryModel:
        pass

    def _calc(_mol, *, query_model=None):
        nonlocal call_count
        _ = query_model
        call_count += 1
        return [_State(7.0)]

    fake_mod.QueryModel = _QueryModel
    fake_mod.calculate_microstate_pka_values = _calc
    monkeypatch.setitem(sys.modules, "pkasolver.query", fake_mod)
    monkeypatch.setattr("molmanager.workers.pka_predictor._patch_pkasolver_dimorphite", lambda: None)
    monkeypatch.setattr("molmanager.workers.pka_predictor._ensure_cairosvg_importable", lambda: None)
    ws = WorkerSignals()
    ps = PKaPredictorSignals()
    finished: list[list[tuple[int | None, str]]] = []
    ps.finished.connect(lambda rows: finished.append(rows))

    m = Chem.MolFromSmiles("CCO")
    assert m is not None
    rows = [(1, Chem.Mol(m)), (2, Chem.Mol(m)), (3, Chem.MolFromSmiles("CCN"))]
    worker = PKaPredictorWorker(rows, ws, ps, cancel_event=_CancelNever())
    worker.run()

    assert call_count == 2
    assert finished
    by_oid = {oid: txt for oid, txt in finished[0]}
    assert by_oid[1] == by_oid[2] == "7.00"
    assert by_oid[3] == "7.00"
