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
    auto_workers = min(n_unique, max(2, min(8, cpu - 1)))
    if configured is None:
        use_mp = n_unique >= 2 and auto_workers > 1
        return use_mp, auto_workers if use_mp else 1
    if configured <= 1:
        return False, 1
    proc_workers = min(int(configured), n_unique, 8)
    return proc_workers > 1 and n_unique >= 2, proc_workers


def _mp_compute_microstates(task: tuple[str, bytes]) -> tuple[str, list | None]:
    """Child-process entry: one structure → pkasolver microstate list (or ``None``)."""
    from chemmanager.pkasolver_descriptor_support import microstates_to_picklable

    from .pka_predictor import (
        _discard_stdio,
        _ensure_cairosvg_importable,
        _patch_pkasolver_dimorphite,
        _quieter_pkasolver_dependency_loggers,
        isolated_sys_argv_for_embedded_cli,
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
                from pkasolver.query import QueryModel, calculate_microstate_pka_values
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
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            qm = QueryModel()
        with _discard_stdio(), isolated_sys_argv_for_embedded_cli():
            states = calculate_microstate_pka_values(safe, query_model=qm)
        if not states:
            return key, None
        return key, microstates_to_picklable(states)
    except Exception:
        logger.exception("pkasolver subprocess: prediction failed for key=%s", key[:48])
        return key, None


def build_microstates_cache_by_key(
    mols: list[Chem.Mol],
    *,
    workers_cfg: int | None = None,
    cancel_event: threading.Event | None = None,
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

    cfg = load_config()
    configured = workers_cfg if workers_cfg is not None else cfg.pka_process_workers
    use_mp, proc_workers = plan_pkasolver_process_workers(len(order), configured)

    from chemmanager.pkasolver_descriptor_support import microstates_for_mol
    from .pka_predictor import _ensure_cairosvg_importable

    _ensure_cairosvg_importable()

    if use_mp:
        tasks = [(k, rep[k].ToBinary()) for k in order]
        cache: dict[str, list | None] = {}
        user_cancelled = False
        pool_failed = False
        ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
        try:
            pending = {ex.submit(_mp_compute_microstates, t) for t in tasks}
            while pending:
                if should_terminate_process_pool(cancel_event):
                    user_cancelled = True
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
        if pool_failed or len(cache) < len(order):
            for key in order:
                if key in cache:
                    continue
                if should_terminate_process_pool(cancel_event):
                    break
                cache[key] = microstates_for_mol(rep[key])
        logger.debug(
            "pkasolver cache: %s unique structure(s), process pool=%s",
            len(order),
            proc_workers,
        )
        return cache

    cache = {}
    for key in order:
        if should_terminate_process_pool(cancel_event):
            break
        cache[key] = microstates_for_mol(rep[key])
    logger.debug("pkasolver cache: %s unique structure(s), sequential", len(order))
    return cache


def build_microstates_cache_for_rows(
    rows: list[tuple[int, Chem.Mol | None]],
    *,
    workers_cfg: int | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[int, list | None]:
    """Map each row index to pkasolver microstates (deduplicated by structure)."""
    mols = [mol for _idx, mol in rows if mol is not None]
    by_key = build_microstates_cache_by_key(
        mols, workers_cfg=workers_cfg, cancel_event=cancel_event
    )
    out: dict[int, list | None] = {}
    for idx, mol in rows:
        if mol is None:
            out[idx] = None
            continue
        out[idx] = by_key.get(structure_key(mol))
    return out
