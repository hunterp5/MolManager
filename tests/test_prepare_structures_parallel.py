"""Tests for Prepare Structures multiprocess helpers."""

from __future__ import annotations

import threading

from rdkit import Chem

from molmanager.prepare_structures_parallel import (
    _mp_neutralize_mol_row,
    _mp_wash_mol_row,
    mol_from_bytes,
    mol_to_bytes,
    run_prepare_tasks_parallel_ordered,
    should_use_prepare_structures_process_pool,
)


def test_should_use_prepare_structures_process_pool():
    assert should_use_prepare_structures_process_pool(100, min_rows=64) is True
    assert should_use_prepare_structures_process_pool(10, min_rows=64) is False
    assert should_use_prepare_structures_process_pool(100, min_rows=0) is False


def test_mol_bytes_round_trip():
    mol = Chem.MolFromSmiles("CCO")
    blob = mol_to_bytes(mol)
    assert blob is not None
    back = mol_from_bytes(blob)
    assert back is not None
    assert back.GetNumAtoms() == mol.GetNumAtoms()


def test_run_prepare_tasks_parallel_ordered_calls_progress_with_total():
    seen: list[tuple[int, int]] = []

    def worker(_task):
        return (1, b"")

    def on_progress(done: int, total: int) -> None:
        seen.append((done, total))

    tasks = [(i,) for i in range(70)]
    run_prepare_tasks_parallel_ordered(
        tasks,
        worker,
        cancel_event=threading.Event(),
        on_progress=on_progress,
    )
    assert seen
    assert seen[-1][1] == 70


def test_mp_wash_and_neutralize_smiles_row():
    mol = Chem.MolFromSmiles("C.CCO")
    blob = mol_to_bytes(mol)
    assert blob is not None
    washed = _mp_wash_mol_row((1, blob, "C.CCO"))
    assert washed is not None
    oid, parent_bytes, fragments = washed
    assert oid == 1
    parent = mol_from_bytes(parent_bytes)
    assert parent is not None
    assert parent.GetNumHeavyAtoms() >= 3
    neutral = _mp_neutralize_mol_row((2, parent_bytes))
    assert neutral is not None
