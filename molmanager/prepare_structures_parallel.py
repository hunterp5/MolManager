"""Multiprocess helpers for Prepare Structures chemistry workers."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import TypeVar

from rdkit import Chem

from .fragment_disconnect import largest_fragment_and_rest
from .structure_hydrogens import add_explicit_hydrogens
from .structure_neutralize import neutralize_mol
from .utils import parse_molecule_from_cell_text

try:
    _PROP_FLAGS = int(Chem.PropertyPickleOptions.AllProps)
except Exception:  # pragma: no cover
    _PROP_FLAGS = None

T = TypeVar("T")


def mol_from_bytes(blob: bytes | None) -> Chem.Mol | None:
    if not blob:
        return None
    try:
        return Chem.Mol(bytes(blob))
    except Exception:
        return None


def mol_to_bytes(mol: Chem.Mol | None) -> bytes | None:
    if mol is None:
        return None
    try:
        if _PROP_FLAGS is not None:
            return mol.ToBinary(_PROP_FLAGS)
        return mol.ToBinary()
    except Exception:
        try:
            return mol.ToBinary()
        except Exception:
            return None


def _mp_wash_mol_row(args: tuple[int, bytes, str | None]) -> tuple[int, bytes, str] | None:
    oid, mol_bytes, source_text = args
    mol = mol_from_bytes(mol_bytes)
    if mol is None:
        return None
    parent, fragments = largest_fragment_and_rest(mol, source_text)
    if parent is None:
        return None
    parent_bytes = mol_to_bytes(parent)
    if parent_bytes is None:
        return None
    return int(oid), parent_bytes, str(fragments or "")


def _mp_wash_smiles_row(args: tuple[int, str]) -> tuple[int, bytes, str] | None:
    oid, raw = args
    text = (raw or "").strip()
    if not text:
        return None
    mol = parse_molecule_from_cell_text(text)
    if mol is None:
        return None
    parent, fragments = largest_fragment_and_rest(mol, text)
    if parent is None:
        return None
    parent_bytes = mol_to_bytes(parent)
    if parent_bytes is None:
        return None
    return int(oid), parent_bytes, str(fragments or "")


def _mp_neutralize_mol_row(args: tuple[int, bytes]) -> tuple[int, bytes] | None:
    oid, mol_bytes = args
    mol = mol_from_bytes(mol_bytes)
    if mol is None:
        return None
    neutral = neutralize_mol(mol)
    out = mol_to_bytes(neutral)
    if out is None:
        return None
    return int(oid), out


def _mp_neutralize_smiles_row(args: tuple[int, str]) -> tuple[int, bytes] | None:
    oid, raw = args
    text = (raw or "").strip()
    if not text:
        return None
    mol = parse_molecule_from_cell_text(text)
    if mol is None:
        return None
    neutral = neutralize_mol(mol)
    out = mol_to_bytes(neutral)
    if out is None:
        return None
    return int(oid), out


def _mp_add_explicit_h_row(args: tuple[int, bytes]) -> tuple[int, bytes] | None:
    oid, mol_bytes = args
    mol = mol_from_bytes(mol_bytes)
    if mol is None:
        return None
    with_h = add_explicit_hydrogens(mol)
    out = mol_to_bytes(with_h)
    if out is None:
        return None
    return int(oid), out


def _mp_add_explicit_h_smiles_row(args: tuple[int, str]) -> tuple[int, bytes] | None:
    oid, raw = args
    text = (raw or "").strip()
    if not text:
        return None
    mol = parse_molecule_from_cell_text(text)
    if mol is None:
        return None
    with_h = add_explicit_hydrogens(mol)
    out = mol_to_bytes(with_h)
    if out is None:
        return None
    return int(oid), out


def should_use_prepare_structures_process_pool(n_rows: int, *, min_rows: int) -> bool:
    return int(min_rows) > 0 and n_rows >= int(min_rows) and (os.cpu_count() or 1) > 1


def process_pool_workers(n_tasks: int) -> int:
    ncpu = os.cpu_count() or 4
    return min(max(2, ncpu - 1), 8, max(1, int(n_tasks)))


def run_prepare_tasks_parallel_ordered(
    tasks: list[T],
    worker_fn: Callable[[T], object | None],
    *,
    cancel_event,
    on_progress: Callable[[int, int], None] | None = None,
) -> list:
    """Run picklable tasks in a process pool; preserve input order among successful rows."""
    if not tasks:
        return []
    total = len(tasks)
    proc_workers = process_pool_workers(total)
    from .workers.process_pool_utils import (
        register_process_pool,
        should_terminate_process_pool,
        shutdown_process_pool_executor,
    )

    ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
    order: dict[int, object] = {}
    done = 0
    last_pulse = 0.0
    futures = [ex.submit(worker_fn, task) for task in tasks]
    pending: dict = {fut: idx for idx, fut in enumerate(futures)}
    pending_futs = set(pending.keys())
    try:
        while pending_futs:
            if should_terminate_process_pool(cancel_event):
                for fut in pending_futs:
                    fut.cancel()
                break
            completed, pending_futs = wait(pending_futs, timeout=0.2, return_when=FIRST_COMPLETED)
            for fut in completed:
                idx = pending[fut]
                if fut.cancelled():
                    continue
                try:
                    row = fut.result()
                    if row is not None:
                        order[idx] = row
                except Exception:
                    pass
                done += 1
                now = time.monotonic()
                if on_progress is not None and (now - last_pulse) >= 0.12:
                    last_pulse = now
                    on_progress(done, total)
    finally:
        shutdown_process_pool_executor(ex, kill_workers=should_terminate_process_pool(cancel_event))
    return [order[i] for i in sorted(order)]
