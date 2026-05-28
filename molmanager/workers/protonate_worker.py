"""Generate dominant protomer per molecule using pkasolver microstates."""

from __future__ import annotations

import logging
import threading
import time
import warnings
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal
from rdkit import Chem

from ..config import load_config
from .pka_predictor import (
    _discard_stdout_only,
    _ensure_cairosvg_importable,
    _patch_pkasolver_dimorphite,
    _quieter_pkasolver_dependency_loggers,
    isolated_sys_argv_for_embedded_cli,
    prepare_mol_for_pkasolver,
)
from .pkasolver_parallel import plan_pkasolver_process_workers
from .process_pool_utils import (
    register_process_pool,
    should_terminate_process_pool,
    shutdown_process_pool_executor,
)
from .protomer_generator import estimate_protomer_populations_from_states
from .structure_grouping import group_rows_by_structure

logger = logging.getLogger(__name__)


class ProtonateSignals(QObject):
    finished = pyqtSignal(list)  # list[tuple[int, str, float]] oid, dominant_smiles, pct
    failed = pyqtSignal(str)


def _dominant_smiles_from_microstates(states, pH: float) -> tuple[str, float] | None:
    pops = estimate_protomer_populations_from_states(states, float(pH))
    if not pops:
        return None
    smi, pct, _mol = pops[0]
    if not smi:
        return None
    return str(smi), float(pct)


def _mp_compute_dominant_smiles(task: tuple[str, bytes, float]) -> tuple[str, tuple[str, float] | None]:
    """Child-process entry: load pkasolver, compute dominant protomer for one unique structure."""
    key, mol_blob, ph = task
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
            logger.exception("Protonate subprocess: pkasolver import failed")
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
        with _discard_stdout_only(), isolated_sys_argv_for_embedded_cli():
            states = calculate_microstate_pka_values(safe, query_model=qm)
        dom = _dominant_smiles_from_microstates(states, ph)
        return key, dom
    except Exception:
        logger.exception("Protonate subprocess: prediction failed for key=%s", key[:48])
        return key, None


class ProtonateWorker(QRunnable):
    """Compute dominant protomer SMILES for each row at a target pH."""

    def __init__(
        self,
        rows: list[tuple[int, Chem.Mol | None]],
        pH: float,
        *,
        signals: ProtonateSignals,
        cancel_event: threading.Event | None = None,
        progress_state=None,
        worker_signals=None,
        progress_message: str = "Protonate",
    ) -> None:
        super().__init__()
        self.rows = rows
        self.pH = float(pH)
        self.signals = signals
        self.cancel_event = cancel_event
        self.progress_state = progress_state
        self.worker_signals = worker_signals
        self.progress_message = progress_message

    def run(self) -> None:
        from ..tool_progress import report_tool_progress

        with _quieter_pkasolver_dependency_loggers():
            try:
                _ensure_cairosvg_importable()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    _patch_pkasolver_dimorphite()
                    from pkasolver.query import QueryModel, calculate_microstate_pka_values
            except Exception as e:
                logger.exception("Protonate: failed to import pkasolver stack")
                self.signals.failed.emit(
                    "Could not load pkasolver (missing PyTorch / torch-geometric / pkasolver?). "
                    f"Details: {e}"
                )
                return

            cancel_ev = self.cancel_event
            order, rep, oids_map = group_rows_by_structure(self.rows)
            n_work = sum(len(oids_map[k]) for k in order)
            tot = max(n_work, 1)
            n_unique = len(order)

            use_mp, proc_workers = plan_pkasolver_process_workers(
                n_unique, load_config().protomer_process_workers
            )

            throttle = [0, 0.0]
            done_cum = 0
            last_pulse = 0.0

            def _emit(done: int, *, force: bool = False) -> None:
                nonlocal last_pulse
                now = time.monotonic()
                if force or done >= tot or (now - last_pulse) >= 0.12:
                    last_pulse = now
                    report_tool_progress(
                        message=self.progress_message,
                        done=min(int(done), tot),
                        total=tot,
                        progress_state=self.progress_state,
                        signals=self.worker_signals,
                        throttle=throttle,
                        force_signal=force,
                    )

            _emit(0, force=True)
            if not order:
                _emit(tot, force=True)
                self.signals.finished.emit([])
                return

            results_by_key: dict[str, tuple[str, float] | None] = {}
            partial: list[tuple[int, str, float]] = []
            cancelled = False

            if use_mp:
                tasks = [(k, rep[k].ToBinary(), self.pH) for k in order if rep.get(k) is not None]
                ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
                try:
                    pending = {ex.submit(_mp_compute_dominant_smiles, t) for t in tasks}
                    while pending:
                        if should_terminate_process_pool(cancel_ev):
                            cancelled = True
                            for f in pending:
                                f.cancel()
                            break
                        completed, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                        for f in completed:
                            if f.cancelled():
                                continue
                            try:
                                key, dom = f.result()
                                k = str(key)
                                results_by_key[k] = dom
                                if dom is not None:
                                    smi, pct = dom
                                    for oid in oids_map.get(k, ()):
                                        partial.append((int(oid), str(smi), float(pct)))
                                done_cum += len(oids_map.get(k, ()))
                            except Exception:
                                logger.exception("Protonate process-pool task failed")
                            _emit(done_cum)
                finally:
                    shutdown_process_pool_executor(
                        ex, kill_workers=should_terminate_process_pool(cancel_ev)
                    )
            else:
                # In-process single QueryModel (locked in pKa predictor; here sequential by design).
                for key in order:
                    if cancel_ev is not None and cancel_ev.is_set():
                        cancelled = True
                        break
                    mol = rep.get(key)
                    if mol is None:
                        results_by_key[key] = None
                        continue
                    safe = prepare_mol_for_pkasolver(mol)
                    if safe is None:
                        results_by_key[key] = None
                        continue
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", FutureWarning)
                            qm = QueryModel()
                        with _discard_stdout_only(), isolated_sys_argv_for_embedded_cli():
                            states = calculate_microstate_pka_values(safe, query_model=qm)
                        results_by_key[key] = _dominant_smiles_from_microstates(states, self.pH)
                    except Exception:
                        results_by_key[key] = None
                    done_cum += len(oids_map.get(key, ()))
                    _emit(done_cum)
                    dom = results_by_key.get(key)
                    if dom is not None:
                        smi, pct = dom
                        for oid in oids_map.get(key, ()):
                            partial.append((int(oid), str(smi), float(pct)))

            _emit(tot, force=True)
            # Partial results behavior: write what we have, then signal cancellation.
            if cancelled and self.worker_signals is not None:
                try:
                    from .signals import emit_partial_results_if_cancelled

                    emit_partial_results_if_cancelled(
                        self.worker_signals, "Protonate", len(partial), tot, True
                    )
                except Exception:
                    pass
            self.signals.finished.emit(partial)
            if cancelled:
                self.signals.failed.emit("Cancelled.")

