"""Fast Prepare: disconnect largest fragment + neutralize in parallel worker processes."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from PyQt5.QtCore import QRunnable
from rdkit import Chem

from ..fragment_disconnect import largest_fragment_and_rest
from ..structure_neutralize import neutralize_mol
from ..utils import parse_molecule_from_cell_text
from .process_pool_utils import (
    register_process_pool,
    should_terminate_process_pool,
    shutdown_process_pool_executor,
)
from .signals import WorkerSignals, emit_partial_results_if_cancelled

logger = logging.getLogger(__name__)


def fast_prepare_one_row(oid: int, mol_bytes: bytes, source_text: str) -> tuple[int, bytes, str, bool]:
    """
    Disconnect the largest fragment, neutralize it, and return picklable row output.

    Returns ``(oid, neutral_mol_bytes, fragments_text, ok)``.
    """
    mol = None
    if mol_bytes:
        try:
            mol = Chem.Mol(mol_bytes)
        except Exception:
            mol = None
    raw = (source_text or "").strip()
    if mol is None and raw:
        mol = parse_molecule_from_cell_text(raw)
    if mol is None:
        return int(oid), b"", "", False
    parent, fragments = largest_fragment_and_rest(mol, raw or None)
    if parent is None:
        return int(oid), b"", fragments, False
    neutral = neutralize_mol(parent)
    if neutral is None:
        return int(oid), b"", fragments, False
    try:
        blob = neutral.ToBinary()
    except Exception:
        return int(oid), b"", fragments, False
    return int(oid), blob, fragments, True


def _mp_fast_prepare_batch(batch: list[tuple[int, bytes, str]]) -> list[tuple[int, bytes, str, bool]]:
    return [fast_prepare_one_row(oid, blob, text) for oid, blob, text in batch]


class FastPrepareWorker(QRunnable):
    """Run disconnect + neutralize for many rows using a process pool."""

    def __init__(
        self,
        rows: list[tuple],
        signals: WorkerSignals,
        *,
        is_smiles: bool = False,
        cancel_event=None,
        batch_size: int = 64,
    ) -> None:
        super().__init__()
        self.rows = list(rows)
        self.signals = signals
        self.is_smiles = bool(is_smiles)
        self.cancel_event = cancel_event
        self.batch_size = max(1, int(batch_size))

    def _row_payloads(self) -> list[tuple[int, bytes, str]]:
        payloads: list[tuple[int, bytes, str]] = []
        for row in self.rows:
            oid = int(row[0])
            if self.is_smiles:
                text = str(row[1] or "").strip()
                payloads.append((oid, b"", text))
                continue
            mol = row[1] if len(row) >= 2 else None
            source_text = (str(row[2]).strip() if len(row) >= 3 and row[2] else "") or ""
            blob = b""
            if mol is not None:
                try:
                    blob = mol.ToBinary()
                except Exception:
                    blob = b""
            payloads.append((oid, blob, source_text))
        return payloads

    def run(self) -> None:
        payloads = self._row_payloads()
        total = max(len(payloads), 1)
        if not payloads:
            self.signals.fast_prepared.emit([])
            return

        ncpu = os.cpu_count() or 4
        proc_workers = min(8, max(2, ncpu - 1), 6)
        batch_size = self.batch_size
        batch_args = [
            payloads[start : start + batch_size]
            for start in range(0, len(payloads), batch_size)
        ]

        results: list[tuple[int, Chem.Mol, str]] = []
        done_count = 0
        cancelled = False
        last_pulse = 0.0
        ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
        pending = set()

        def _emit_progress(done: int, *, force: bool = False) -> None:
            nonlocal last_pulse
            now = time.monotonic()
            if not force and (now - last_pulse) < 0.12 and done < total:
                return
            last_pulse = now
            try:
                self.signals.tool_progress.emit("Fast prepare…", done, total)
            except Exception:
                pass

        try:
            for batch in batch_args:
                pending.add(ex.submit(_mp_fast_prepare_batch, batch))
            _emit_progress(0, force=True)
            while pending:
                if should_terminate_process_pool(self.cancel_event):
                    cancelled = True
                    for fut in list(pending):
                        if fut.done() and not fut.cancelled():
                            try:
                                for oid, blob, fragments, ok in fut.result():
                                    done_count += 1
                                    if ok and blob:
                                        results.append((oid, Chem.Mol(blob), fragments))
                            except Exception:
                                logger.exception("Fast prepare batch failed during cancel drain")
                        else:
                            fut.cancel()
                    break
                completed, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for fut in completed:
                    if fut.cancelled():
                        continue
                    try:
                        for oid, blob, fragments, ok in fut.result():
                            done_count += 1
                            if ok and blob:
                                results.append((oid, Chem.Mol(blob), fragments))
                        _emit_progress(done_count)
                    except Exception:
                        logger.exception("Fast prepare batch failed")
        finally:
            shutdown_process_pool_executor(
                ex, kill_workers=should_terminate_process_pool(self.cancel_event)
            )

        emit_partial_results_if_cancelled(
            self.signals, "Fast prepare", done_count, total, cancelled
        )
        self.signals.fast_prepared.emit(results)
