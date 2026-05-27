"""pkasolver parallel planning and structure cache."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import pytest
from rdkit import Chem

import molmanager.workers.pkasolver_parallel as pkasolver_parallel
from molmanager.pkasolver_descriptor_support import PicklableMicrostate, microstates_to_picklable
from molmanager.workers.pkasolver_parallel import (
    _mp_compute_microstates,
    plan_pkasolver_process_workers,
)


@pytest.fixture(autouse=True)
def _force_sequential_pkasolver_cache(monkeypatch) -> None:
    """Tests that mock ``microstates_for_mol`` must not spawn a real process pool."""
    monkeypatch.setenv("MOLMANAGER_PKA_PROCESS_WORKERS", "1")
    monkeypatch.setattr(
        pkasolver_parallel,
        "plan_pkasolver_process_workers",
        lambda _n, _c: (False, 1),
    )


def test_plan_pkasolver_auto_uses_mp_from_two_unique() -> None:
    use_mp, workers = plan_pkasolver_process_workers(3, None)
    assert use_mp is True
    assert workers >= 2


def test_plan_pkasolver_respects_force_sequential() -> None:
    use_mp, workers = plan_pkasolver_process_workers(10, 1)
    assert workers == 1


def test_build_microstates_cache_dedupes(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_microstates(mol):
        from molmanager.workers.structure_grouping import structure_key

        calls.append(structure_key(mol))
        return [{"pka": 7.0}]

    monkeypatch.setattr(
        "molmanager.pkasolver_descriptor_support.microstates_for_mol",
        _fake_microstates,
    )

    m = Chem.MolFromSmiles("CCO")
    assert m is not None
    cache = pkasolver_parallel.build_microstates_cache_by_key(
        [Chem.Mol(m), Chem.Mol(m), Chem.MolFromSmiles("CCN")]
    )
    assert len(calls) == 2
    assert len(cache) == 2


def test_mp_microstates_return_picklable_snapshots() -> None:
    """Process-pool results must not embed pkasolver types (parent cannot unpickle them)."""
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    task = ("CCO", mol.ToBinary())
    with ProcessPoolExecutor(max_workers=1) as ex:
        key, states = ex.submit(_mp_compute_microstates, task).result()
    assert key == "CCO"
    if states is not None:
        assert isinstance(states[0], PicklableMicrostate)


def test_microstates_to_picklable_roundtrip() -> None:
    from types import SimpleNamespace

    pm = Chem.MolFromSmiles("CCO")
    dm = Chem.MolFromSmiles("CC[O-]")
    assert pm is not None and dm is not None
    raw = [SimpleNamespace(pka=15.9, protonated_mol=pm, deprotonated_mol=dm, ph7_mol=pm)]
    snap = microstates_to_picklable(raw)
    assert snap[0].pka == 15.9
    assert snap[0].protonated_mol is not None
