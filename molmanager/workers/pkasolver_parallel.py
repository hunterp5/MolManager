# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

"""Shared pkasolver deduplication and optional process-pool execution."""

from __future__ import annotations

import logging
import os
import threading
import warnings
from concurrent.futures import FIRST_COMPLETED, BrokenExecutor, ProcessPoolExecutor, wait

from .process_pool_utils import (
    register_process_pool,
    should_terminate_process_pool,
    shutdown_process_pool_executor,
)

from rdkit import Chem

from ..config import load_config
from .structure_grouping import group_rows_by_structure, structure_key

logger = logging.getLogger(__name__)


def plan_pkasolver_process_workers(
    n_unique: int,
    configured: int | None,
) -> tuple[bool, int]:
    """
    Decide whether to use a process pool and how many workers.

    ``configured`` is the tool-specific env override (``None`` = auto).
    """
    cpu = os.cpu_count() or 4
    # Prefer offloading pkasolver into a child process whenever possible so the Qt GUI thread
    # can keep polling/updating tool progress even if pkasolver dependencies hold the GIL.
    # For n_unique == 1 this uses a 1-worker pool (still isolates heavy work).
    auto_workers = min(n_unique, max(1, min(8, cpu - 1)))
    if configured is None:
        use_mp = cpu > 1 and n_unique >= 1
        return use_mp, auto_workers if use_mp else 1
    # Config semantics:
    # - <= 0: force in-process execution
    # - 1: keep sequential behavior, but still isolate in a child process when possible so UI progress updates.
    # - >= 2: parallelize across processes (bounded).
    cfg_i = int(configured)
    if cfg_i <= 0:
        return False, 1
    if cfg_i == 1:
        return cpu > 1 and n_unique >= 1, 1
    proc_workers = min(cfg_i, n_unique, 8)
    return proc_workers > 1 and n_unique >= 2, proc_workers


def _mp_compute_microstates(task: tuple[str, bytes]) -> tuple[str, list | None]:
    """Child-process entry: one structure → pkasolver microstate list (or ``None``)."""
    from molmanager.pkasolver_descriptor_support import microstates_to_picklable

    from .pka_predictor import (
        _discard_stdio,
        _ensure_cairosvg_importable,
        _patch_pkasolver_dimorphite,
        _quieter_pkasolver_dependency_loggers,
        get_worker_query_model,
        isolated_sys_argv_for_embedded_cli,
        pkasolver_inference_mode,
        prepare_mol_for_pkasolver,
    )

    key, mol_blob = task
    if not mol_blob:
        return key, None
    with _quieter_pkasolver_dependency_loggers():
        try:
            _ensure_cairosvg_importable()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                _patch_pkasolver_dimorphite()
                from pkasolver.query import calculate_microstate_pka_values
            qm = get_worker_query_model()
        except Exception:
            logger.exception("pkasolver subprocess: import failed")
            return key, None
    try:
        mol = Chem.Mol(mol_blob)
    except Exception:
        return key, None
    if mol is None or mol.GetNumAtoms() == 0:
        return key, None
    safe = prepare_mol_for_pkasolver(mol)
    if safe is None:
        return key, None
    try:
        with pkasolver_inference_mode(), _discard_stdio(), isolated_sys_argv_for_embedded_cli():
            states = calculate_microstate_pka_values(safe, query_model=qm)
        if not states:
            return key, None
        return key, microstates_to_picklable(states)
    except Exception:
        logger.exception("pkasolver subprocess: prediction failed for key=%s", key[:48])
        return key, None


def predict_microstates_for_sketch(
    mol: Chem.Mol,
    *,
    cancel_event: threading.Event | None = None,
    timeout_s: float = 180.0,
) -> list | None:
    """
    Predict microstates for one sketcher molecule without blocking the Qt GUI on the GIL.

    Uses the session microstate cache when possible. Otherwise runs pkasolver in a
    short-lived child process (no in-process fallback — failures return ``None``).
    """
    import time

    from molmanager.microstate_cache import lookup as cache_lookup
    from molmanager.microstate_cache import store as cache_store

    key = structure_key(mol)
    hit, cached = cache_lookup(key)
    if hit:
        return cached

    try:
        blob = mol.ToBinary()
    except Exception:
        return None
    if not blob:
        return None

    cfg = load_config()
    # Prefer a child process whenever possible so pkasolver/PyTorch cannot freeze Qt via the GIL.
    # ``MOLMANAGER_PKA_PROCESS_WORKERS<=0`` forces in-process (tests / constrained environments).
    configured = cfg.pka_process_workers
    if configured is not None and int(configured) <= 0:
        from molmanager.pkasolver_descriptor_support import microstates_for_mol

        return microstates_for_mol(mol)

    ex = register_process_pool(ProcessPoolExecutor(max_workers=1))
    states: list | None = None
    timed_out = False
    try:
        fut = ex.submit(_mp_compute_microstates, (key, blob))
        deadline = time.monotonic() + max(5.0, float(timeout_s))
        while True:
            if should_terminate_process_pool(cancel_event):
                fut.cancel()
                states = None
                break
            if time.monotonic() >= deadline:
                logger.warning("pkasolver sketch microstates timed out after %.0fs", timeout_s)
                fut.cancel()
                timed_out = True
                states = None
                break
            completed, _pending = wait({fut}, timeout=0.25, return_when=FIRST_COMPLETED)
            if not completed:
                continue
            if fut.cancelled():
                states = None
                break
            try:
                _k, states = fut.result()
            except BrokenExecutor:
                logger.warning("pkasolver sketch process pool broke during prediction")
                timed_out = True
                states = None
            except Exception:
                logger.debug("pkasolver sketch microstates failed", exc_info=True)
                states = None
            break
    finally:
        shutdown_process_pool_executor(
            ex,
            kill_workers=should_terminate_process_pool(cancel_event) or timed_out,
        )

    cache_store(key, states)
    return states


def build_microstates_cache_by_key(
    mols: list[Chem.Mol],
    *,
    workers_cfg: int | None = None,
    cancel_event: threading.Event | None = None,
    progress_state=None,
    signals=None,
    progress_message: str = "pkasolver microstates…",
    progress_total: int | None = None,
) -> dict[str, list | None]:
    """
    Predict pkasolver microstates once per unique structure.

    Returns ``structure_key → microstate list`` (or ``None`` on failure).
    """
    rows = [(None, m) for m in mols if m is not None]
    if not rows:
        return {}
    order, rep, _oids_map = group_rows_by_structure(rows)
    if not order:
        return {}

    from molmanager.microstate_cache import lookup as cache_lookup
    from molmanager.microstate_cache import store_many as cache_store_many
    from molmanager.pkasolver_descriptor_support import microstates_for_mol
    from .pka_predictor import _ensure_cairosvg_importable
    from ..tool_progress import report_tool_progress

    cache: dict[str, list | None] = {}
    need: list[str] = []
    for key in order:
        hit, states = cache_lookup(key)
        if hit:
            cache[key] = states
        else:
            need.append(key)

    cfg = load_config()
    configured = workers_cfg if workers_cfg is not None else cfg.pka_process_workers
    use_mp, proc_workers = plan_pkasolver_process_workers(len(need), configured) if need else (False, 1)
    n_unique = len(order)
    n_need = len(need)

    _ensure_cairosvg_importable()

    def _report_pkasolver(done_unique: int, *, force: bool = False) -> None:
        # Map pkasolver progress onto a stable total so the GUI status bar behaves like other
        # descriptor jobs (never “stuck at 100%” mid-computation).
        if progress_total is None:
            done_mapped = done_unique
            total_mapped = n_unique
        else:
            tot = max(1, int(progress_total))
            # Reserve the final step for the actual descriptor pass.
            ceiling = max(0, tot - 1)
            if n_unique <= 0:
                done_mapped = 0
            elif ceiling <= 0:
                done_mapped = 0
            else:
                done_mapped = int((float(done_unique) / float(n_unique)) * float(ceiling))
                done_mapped = max(0, min(done_mapped, ceiling))
            total_mapped = tot
        report_tool_progress(
            message=progress_message,
            done=done_mapped,
            total=total_mapped,
            progress_state=progress_state,
            signals=signals,
            force_signal=force,
        )

    _report_pkasolver(len(cache), force=True)

    if not need:
        _report_pkasolver(len(cache), force=True)
        logger.debug("pkasolver cache: %s unique structure(s), all session-cache hits", n_unique)
        return cache

    if use_mp:
        tasks = [(k, rep[k].ToBinary()) for k in need]
        pool_failed = False
        ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
        try:
            pending = {ex.submit(_mp_compute_microstates, t) for t in tasks}
            while pending:
                if should_terminate_process_pool(cancel_event):
                    for f in pending:
                        f.cancel()
                    break
                completed, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                for f in completed:
                    if f.cancelled():
                        continue
                    try:
                        key, states = f.result()
                        cache[key] = states
                        _report_pkasolver(len(cache))
                    except BrokenExecutor:
                        pool_failed = True
                        logger.warning(
                            "pkasolver process pool failed; finishing remaining structures sequentially"
                        )
                        break
                    except Exception:
                        logger.debug("pkasolver process-pool task failed", exc_info=True)
                if pool_failed:
                    for f in pending:
                        f.cancel()
                    break
        finally:
            shutdown_process_pool_executor(
                ex, kill_workers=should_terminate_process_pool(cancel_event)
            )
        if pool_failed or any(k not in cache for k in need):
            for key in need:
                if key in cache:
                    continue
                if should_terminate_process_pool(cancel_event):
                    break
                cache[key] = microstates_for_mol(rep[key])
                _report_pkasolver(len(cache))
        _report_pkasolver(len(cache), force=True)
        cache_store_many({k: cache[k] for k in need if k in cache})
        logger.debug(
            "pkasolver cache: %s unique (%s missed session cache), process pool=%s",
            n_unique,
            n_need,
            proc_workers,
        )
        return cache

    for i, key in enumerate(need, start=1):
        if should_terminate_process_pool(cancel_event):
            break
        cache[key] = microstates_for_mol(rep[key])
        _report_pkasolver(len(cache))
    _report_pkasolver(len(cache), force=True)
    cache_store_many({k: cache[k] for k in need if k in cache})
    logger.debug(
        "pkasolver cache: %s unique (%s missed session cache), sequential",
        n_unique,
        n_need,
    )
    return cache


def build_microstates_cache_for_rows(
    rows: list[tuple[int, Chem.Mol | None]],
    *,
    workers_cfg: int | None = None,
    cancel_event: threading.Event | None = None,
    progress_state=None,
    signals=None,
    progress_message: str = "pkasolver microstates…",
    progress_total: int | None = None,
) -> dict[int, list | None]:
    """Map each row index to pkasolver microstates (deduplicated by structure)."""
    mols = [mol for _idx, mol in rows if mol is not None]
    by_key = build_microstates_cache_by_key(
        mols,
        workers_cfg=workers_cfg,
        cancel_event=cancel_event,
        progress_state=progress_state,
        signals=signals,
        progress_message=progress_message,
        progress_total=progress_total,
    )
    out: dict[int, list | None] = {}
    for idx, mol in rows:
        if mol is None:
            out[idx] = None
            continue
        out[idx] = by_key.get(structure_key(mol))
    return out
