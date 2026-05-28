"""MaxMin diverse subset selection from molecular fingerprints."""

from __future__ import annotations

import logging
import os
import pickle
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any

import numpy as np
from PyQt5.QtCore import QRunnable
from rdkit import Chem
from rdkit import DataStructs
from rdkit.SimDivFilters.rdSimDivPickers import MaxMinPicker

from ..config import load_config
from ..fingerprint_cache import get as cache_get
from ..fingerprint_cache import store as cache_store
from ..rdkit_fingerprints import (
    fingerprint_bitvect_for_ui_choice,
    fingerprint_is_gil_heavy,
    spec_for_label,
)
from ..tool_progress import report_tool_progress
from .process_pool_utils import (
    register_process_pool,
    should_terminate_process_pool,
    shutdown_process_pool_executor,
)
from .signals import DiverseSubsetSignals

logger = logging.getLogger(__name__)

_PROGRESS_LABEL = "Diverse subset"
_PROCESS_POOL_MIN_ROWS = 64


class _Cancelled(Exception):
    pass


@dataclass
class DiverseSubsetPoolRow:
    oid: int
    mol: Chem.Mol | None
    fp: Any | None = None


def _tanimoto_distance(fp_i, fp_j) -> float:
    s = float(DataStructs.TanimotoSimilarity(fp_i, fp_j))
    d = 1.0 - s
    if d < 0.0:
        return 0.0
    if d > 1.0:
        return 1.0
    return d


def _parse_onbits_cell(text: str) -> bool:
    t = (text or "").strip()
    if not t or t.upper() == "N/A":
        return False
    try:
        int(float(t))
    except ValueError:
        return False
    return True


def build_diverse_subset_pool(
    rows: list[tuple[int, Chem.Mol]],
    fp_choice: str,
    *,
    onbits_by_oid: dict[int, str] | None = None,
    require_onbits_column: bool = False,
) -> tuple[list[DiverseSubsetPoolRow], str | None]:
    """
    Build the compound pool for MaxMin picking.

    When ``onbits_by_oid`` is set (matching descriptor on-bits column), only rows with
    valid on-bits values are included. Cached bit vectors are attached when available.
    """
    spec = spec_for_label(fp_choice)
    if spec is None:
        return [], "Unknown fingerprint type."
    internal_key = spec.internal_key
    pool: list[DiverseSubsetPoolRow] = []
    for oid, mol in rows:
        if require_onbits_column and onbits_by_oid is not None:
            if not _parse_onbits_cell(onbits_by_oid.get(int(oid), "")):
                continue
        fp = cache_get(int(oid), internal_key)
        pool.append(DiverseSubsetPoolRow(oid=int(oid), mol=mol, fp=fp))
    return pool, None


def _first_pick_index(n: int, seed: int) -> int:
    if n <= 0:
        return 0
    if seed >= 0:
        return int(seed % n)
    return 0


def maxmin_diverse_pick_bulk(
    fps: list,
    pick_size: int,
    *,
    seed: int = -1,
    cancel_event: threading.Event | None = None,
    on_pick: Callable[[int], None] | None = None,
) -> list[int]:
    """
    Fast MaxMin using :func:`DataStructs.BulkTanimotoSimilarity` each iteration.

    Requires a complete fingerprint list (no lazy generation during picking).
    """
    n = len(fps)
    if pick_size <= 0:
        return []
    if pick_size >= n:
        return list(range(n))
    k = int(pick_size)
    first = _first_pick_index(n, seed)
    picked = [first]
    min_dist = np.full(n, np.inf, dtype=np.float32)
    min_dist[first] = 0.0
    picked_mask = np.zeros(n, dtype=bool)
    picked_mask[first] = True
    progress_step = max(1, k // 200)

    for step in range(1, k):
        if cancel_event is not None and cancel_event.is_set():
            raise _Cancelled()
        last = picked[-1]
        sims = DataStructs.BulkTanimotoSimilarity(fps[last], fps)
        dists = np.subtract(1.0, np.asarray(sims, dtype=np.float32))
        np.minimum(min_dist, dists, out=min_dist)
        min_dist[picked_mask] = -1.0
        nxt = int(np.argmax(min_dist))
        picked.append(nxt)
        picked_mask[nxt] = True
        if on_pick is not None and (step + 1) % progress_step == 0:
            on_pick(step + 1)
    if on_pick is not None:
        on_pick(k)
    return picked


def maxmin_diverse_pick_lazy(
    pool: list[DiverseSubsetPoolRow],
    fp_choice: str,
    pick_size: int,
    *,
    internal_key: str | None = None,
    seed: int = -1,
    first_picks: tuple[int, ...] = (),
    cancel_event: threading.Event | None = None,
    on_fp_computed: Callable[[], None] | None = None,
) -> list[int]:
    """
    MaxMin via RDKit :meth:`MaxMinPicker.LazyPick` (small pools only).

    Returns indices into ``pool``.
    """
    n = len(pool)
    if pick_size <= 0:
        return []
    if pick_size >= n:
        return list(range(n))
    k = int(pick_size)
    spec_key = internal_key
    if spec_key is None:
        spec = spec_for_label(fp_choice)
        spec_key = spec.internal_key if spec is not None else ""

    fp_by_index: dict[int, Any] = {}
    for i, row in enumerate(pool):
        if row.fp is not None:
            fp_by_index[i] = row.fp

    def get_fp(i: int) -> Any:
        if i in fp_by_index:
            return fp_by_index[i]
        if cancel_event is not None and cancel_event.is_set():
            raise _Cancelled()
        row = pool[i]
        mol = row.mol
        if mol is None:
            raise ValueError(f"No structure for OID {row.oid}")
        fp = fingerprint_bitvect_for_ui_choice(mol, fp_choice)
        if fp is None:
            raise ValueError(f"Could not compute fingerprint for OID {row.oid}")
        fp_by_index[i] = fp
        if spec_key:
            cache_store(row.oid, spec_key, fp)
        if on_fp_computed is not None:
            on_fp_computed()
        return fp

    def dist(i: int, j: int) -> float:
        if cancel_event is not None and cancel_event.is_set():
            raise _Cancelled()
        if i == j:
            return 0.0
        return _tanimoto_distance(get_fp(i), get_fp(j))

    picker = MaxMinPicker()
    picks = picker.LazyPick(
        distFunc=dist,
        poolSize=n,
        pickSize=k,
        firstPicks=tuple(int(x) for x in first_picks),
        seed=int(seed),
    )
    return [int(i) for i in picks]


def maxmin_diverse_pick_indices(
    fps: list,
    pick_size: int,
    *,
    seed: int = -1,
    first_picks: tuple[int, ...] = (),
) -> list[int]:
    """MaxMin on precomputed fingerprints (tests and small pools)."""
    if len(fps) >= 128 or pick_size >= 256:
        return maxmin_diverse_pick_bulk(fps, pick_size, seed=seed)
    n = len(fps)
    if pick_size <= 0:
        return []
    if pick_size >= n:
        return list(range(n))

    def dist(i: int, j: int) -> float:
        if i == j:
            return 0.0
        return _tanimoto_distance(fps[i], fps[j])

    picker = MaxMinPicker()
    picks = picker.LazyPick(
        distFunc=dist,
        poolSize=n,
        pickSize=int(pick_size),
        firstPicks=tuple(int(x) for x in first_picks),
        seed=int(seed),
    )
    return [int(i) for i in picks]


def _mp_fp_batch(args: tuple) -> list[tuple[int, Any | None]]:
    """Compute fingerprints for a batch of pool indices in a child process."""
    items, fp_choice = args
    out: list[tuple[int, Any | None]] = []
    for idx, mol_bytes in items:
        if not mol_bytes:
            out.append((int(idx), None))
            continue
        try:
            mol = Chem.Mol(mol_bytes)
            fp = fingerprint_bitvect_for_ui_choice(mol, str(fp_choice)) if mol else None
        except Exception:
            fp = None
        out.append((int(idx), fp))
    return out


def materialize_pool_fingerprints(
    pool: list[DiverseSubsetPoolRow],
    fp_choice: str,
    internal_key: str,
    *,
    cancel_event: threading.Event | None = None,
    on_fp_done: Callable[[int, int], None] | None = None,
    use_process_pool: bool = False,
) -> tuple[list[Any], int, int]:
    """
    Return a full ``fps`` list aligned with ``pool``.

    ``on_fp_done(done, total)`` is called as fingerprints are resolved.
    """
    n = len(pool)
    fps: list[Any | None] = [row.fp for row in pool]
    n_cached = sum(1 for fp in fps if fp is not None)
    missing: list[tuple[int, DiverseSubsetPoolRow]] = [
        (i, row) for i, row in enumerate(pool) if fps[i] is None
    ]
    if not missing:
        if on_fp_done is not None:
            on_fp_done(n, n)
        return fps, n_cached, 0

    n_computed = 0
    mp_ok = use_process_pool and len(missing) >= 32

    if mp_ok:
        batch_size = max(1, int(load_config().descriptor_process_pool_batch_size))
        items = [
            (i, row.mol.ToBinary() if row.mol is not None else b"") for i, row in missing
        ]
        batches = [items[s : s + batch_size] for s in range(0, len(items), batch_size)]
        proc_workers = min(max(2, (os.cpu_count() or 4) - 1), 8)
        ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
        try:
            pending = {
                ex.submit(_mp_fp_batch, (batch, fp_choice)): batch for batch in batches
            }
            while pending:
                if should_terminate_process_pool(cancel_event):
                    raise _Cancelled()
                completed, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                for fut in completed:
                    if fut.cancelled():
                        continue
                    for idx, fp in fut.result():
                        if fp is None:
                            continue
                        fps[idx] = fp
                        cache_store(pool[idx].oid, internal_key, fp)
                        n_computed += 1
                        if on_fp_done is not None:
                            on_fp_done(n_cached + n_computed, n)
        finally:
            shutdown_process_pool_executor(
                ex, kill_workers=should_terminate_process_pool(cancel_event)
            )
    else:
        max_workers = min(8, max(1, (os.cpu_count() or 4)))

        def _one(item: tuple[int, DiverseSubsetPoolRow]) -> tuple[int, Any | None]:
            i, row = item
            if row.mol is None:
                return i, None
            try:
                return i, fingerprint_bitvect_for_ui_choice(row.mol, fp_choice)
            except Exception:
                return i, None

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            pending = {ex.submit(_one, item): item for item in missing}
            while pending:
                if cancel_event is not None and cancel_event.is_set():
                    raise _Cancelled()
                completed, pending = wait(pending, timeout=0.08, return_when=FIRST_COMPLETED)
                for fut in completed:
                    if fut.cancelled():
                        continue
                    idx, fp = fut.result()
                    if fp is not None:
                        fps[idx] = fp
                        cache_store(pool[idx].oid, internal_key, fp)
                    n_computed += 1
                    if on_fp_done is not None:
                        on_fp_done(n_cached + n_computed, n)

    bad = [pool[i].oid for i, fp in enumerate(fps) if fp is None]
    if bad:
        raise ValueError(
            f"Could not compute fingerprints for {len(bad)} row(s) in this scope."
        )
    if on_fp_done is not None:
        on_fp_done(n, n)
    return fps, n_cached, n_computed


def run_diverse_subset_pick(
    pool: list[DiverseSubsetPoolRow],
    fp_choice: str,
    pick_size: int,
    internal_key: str,
    *,
    seed: int = -1,
    cancel_event: threading.Event | None = None,
    on_fp_done: Callable[[int, int], None] | None = None,
    on_pick_done: Callable[[int, int], None] | None = None,
    use_process_pool: bool = False,
) -> tuple[list[int], int, int]:
    """Materialize fingerprints, then run bulk MaxMin. Returns (pick indices, n_cached, n_computed)."""
    n = len(pool)
    pick_k = min(int(pick_size), n)
    fps, n_cached, n_computed = materialize_pool_fingerprints(
        pool,
        fp_choice,
        internal_key,
        cancel_event=cancel_event,
        on_fp_done=on_fp_done,
        use_process_pool=use_process_pool,
    )
    fp_base = n

    def _on_pick(done: int) -> None:
        if on_pick_done is not None:
            on_pick_done(fp_base + done, fp_base + pick_k)

    pick_idx = maxmin_diverse_pick_bulk(
        fps,
        pick_k,
        seed=seed,
        cancel_event=cancel_event,
        on_pick=_on_pick,
    )
    return pick_idx, n_cached, n_computed


def _mp_diverse_subset_pick(args: tuple) -> tuple[list[tuple[int, str]], int, int]:
    """
    Child-process diverse subset: parallel fingerprinting + bulk MaxMin.

    Returns ``(column_rows, n_cache_used, n_computed)``.
    """
    packed_rows, fp_choice, internal_key, pick_k, seed = args
    pool: list[DiverseSubsetPoolRow] = []
    n_cache = 0
    for oid, mol_bytes, fp_bytes in packed_rows:
        mol = Chem.Mol(mol_bytes) if mol_bytes else None
        fp = pickle.loads(fp_bytes) if fp_bytes else None
        if fp is not None:
            n_cache += 1
        pool.append(DiverseSubsetPoolRow(oid=int(oid), mol=mol, fp=fp))

    pick_idx, n_cache_resolved, n_computed = run_diverse_subset_pick(
        pool,
        fp_choice,
        pick_k,
        internal_key,
        seed=seed,
        use_process_pool=True,
    )
    column_rows = [(pool[i].oid, str(rank + 1)) for rank, i in enumerate(pick_idx)]
    return column_rows, n_cache_resolved, n_computed


def _diverse_subset_process_pool_min_rows() -> int:
    cfg = load_config()
    return int(cfg.descriptor_fp_process_pool_min_rows or _PROCESS_POOL_MIN_ROWS)


class DiverseSubsetWorker(QRunnable):
    """Pick a maximally diverse subset by fingerprint MaxMin (off the UI thread)."""

    def __init__(
        self,
        rows: list[tuple[int, Chem.Mol]],
        fp_choice: str,
        subset_size: int,
        signals: DiverseSubsetSignals,
        *,
        onbits_by_oid: dict[int, str] | None = None,
        use_onbits_column: bool = False,
        cancel_event: threading.Event | None = None,
        progress_state=None,
    ):
        super().__init__()
        self.rows = rows
        self.fp_choice = fp_choice
        self.subset_size = max(0, int(subset_size))
        self.signals = signals
        self.onbits_by_oid = onbits_by_oid
        self.use_onbits_column = bool(use_onbits_column)
        self.cancel_event = cancel_event
        self.progress_state = progress_state

    def _report(self, done: int, total: int, *, force: bool = False) -> None:
        report_tool_progress(
            message=_PROGRESS_LABEL,
            done=done,
            total=total,
            progress_state=self.progress_state,
            force_signal=force,
        )

    def run(self) -> None:
        try:
            cancel_ev = self.cancel_event
            spec = spec_for_label(self.fp_choice)
            if spec is None:
                self.signals.failed.emit("Unknown fingerprint type.")
                return

            pool, err = build_diverse_subset_pool(
                self.rows,
                self.fp_choice,
                onbits_by_oid=self.onbits_by_oid,
                require_onbits_column=self.use_onbits_column,
            )
            if err:
                self.signals.failed.emit(err)
                return

            n = len(pool)
            if n == 0:
                if self.use_onbits_column:
                    self.signals.failed.emit(
                        "No rows with valid on-bits values in the matching fingerprint column."
                    )
                else:
                    self.signals.failed.emit("No rows in scope.")
                return

            k = self.subset_size
            if k < 1:
                self.signals.failed.emit("Subset size must be at least 1.")
                return

            pick_k = min(k, n)
            internal_key = spec.internal_key
            progress_total = n + pick_k

            if cancel_ev is not None and cancel_ev.is_set():
                self.signals.failed.emit("Cancelled.")
                return

            use_mp = n >= _diverse_subset_process_pool_min_rows()
            column_rows: list[tuple[int, str]]
            n_cache = 0
            n_computed = 0

            self._report(0, progress_total, force=True)

            if use_mp:
                packed = []
                for row in pool:
                    mol_bytes = row.mol.ToBinary() if row.mol is not None else b""
                    fp_bytes = pickle.dumps(row.fp) if row.fp is not None else b""
                    packed.append((row.oid, mol_bytes, fp_bytes))
                ex = register_process_pool(ProcessPoolExecutor(max_workers=1))
                try:
                    fut = ex.submit(
                        _mp_diverse_subset_pick,
                        (packed, self.fp_choice, internal_key, pick_k, -1),
                    )
                    last_pulse = 0.0
                    while not fut.done():
                        if should_terminate_process_pool(cancel_ev):
                            fut.cancel()
                            self.signals.failed.emit("Cancelled.")
                            return
                        now = time.monotonic()
                        if now - last_pulse >= 0.4:
                            last_pulse = now
                            self._report(0, progress_total, force=True)
                        try:
                            fut.result(timeout=0.2)
                        except TimeoutError:
                            continue
                    column_rows, n_cache, n_computed = fut.result()
                except _Cancelled:
                    self.signals.failed.emit("Cancelled.")
                    return
                except Exception as e:
                    if should_terminate_process_pool(cancel_ev):
                        self.signals.failed.emit("Cancelled.")
                        return
                    logger.exception("Diverse subset process pool failed")
                    self.signals.failed.emit(str(e) or "Diverse subset failed.")
                    return
                finally:
                    shutdown_process_pool_executor(
                        ex, kill_workers=should_terminate_process_pool(cancel_ev)
                    )
            else:
                use_fp_mp = (
                    fingerprint_is_gil_heavy(self.fp_choice)
                    or n >= _PROCESS_POOL_MIN_ROWS
                )

                def _on_fp(done: int, _tot: int) -> None:
                    self._report(min(done, n), progress_total)

                def _on_pick(done: int, _tot: int) -> None:
                    self._report(min(n + done, progress_total), progress_total)

                try:
                    pick_idx, n_cache, n_computed = run_diverse_subset_pick(
                        pool,
                        self.fp_choice,
                        pick_k,
                        internal_key,
                        cancel_event=cancel_ev,
                        on_fp_done=_on_fp,
                        on_pick_done=_on_pick,
                        use_process_pool=use_fp_mp,
                    )
                except _Cancelled:
                    self.signals.failed.emit("Cancelled.")
                    return
                column_rows = [(pool[i].oid, str(rank + 1)) for rank, i in enumerate(pick_idx)]

            picked_oids = [oid for oid, _ in column_rows]
            self._report(progress_total, progress_total, force=True)
            self.signals.finished.emit(picked_oids, column_rows, n_cache, n_computed)
        except Exception as e:
            logger.exception("DiverseSubsetWorker failed")
            self.signals.failed.emit(str(e))
