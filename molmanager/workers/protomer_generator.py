"""Generate protomer sets from pkasolver microstate pKas (approximate populations at a target pH).

Microstate pKas: **pkasolver** (Mayr et al., Front. Chem. 2022, doi:10.3389/fchem.2022.866585;
https://github.com/mayrf/pkasolver). Enumeration path matches the pKa predictor (Dimorphite-DL;
Ropp et al., J. Cheminform. 2019, doi:10.1186/s13321-019-0336-9).

Population math: independent-site Henderson–Hasselbalch pooling over those microstates (same
neutral-fraction idea as LogD 7.4 / LogS 7.4 descriptors; approximate). See ``science_citations``.
"""

from __future__ import annotations

import logging
import threading
import time
import warnings
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from .process_pool_utils import (
    register_process_pool,
    should_terminate_process_pool,
    shutdown_process_pool_executor,
)

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal
from rdkit import Chem

from ..utils import mol_to_canonical_smiles
from .structure_grouping import group_rows_by_structure
from .pka_predictor import (
    _discard_stdout_only,
    _ensure_cairosvg_importable,
    _patch_pkasolver_dimorphite,
    _quieter_pkasolver_dependency_loggers,
    _safe_emit,
    isolated_sys_argv_for_embedded_cli,
    prepare_mol_for_pkasolver,
)

logger = logging.getLogger(__name__)


def _mp_compute_protomer_smiles_pct(task: tuple[str, bytes, float]) -> tuple[str, list[tuple[str, float]]]:
    """
    Child-process entry: load pkasolver, run one structure, return SMILES + approximate %.

    Each process keeps its own ``QueryModel`` so jobs can run in parallel (RAM trade-off).
    """
    key, mol_blob, ph = task
    if not mol_blob:
        return key, []
    with _quieter_pkasolver_dependency_loggers():
        try:
            _ensure_cairosvg_importable()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                _patch_pkasolver_dimorphite()
                from pkasolver.query import QueryModel, calculate_microstate_pka_values
        except Exception:
            logger.exception("Protomer subprocess: pkasolver import failed")
            return key, []
    try:
        mol = Chem.Mol(mol_blob)
    except Exception:
        return key, []
    if mol is None or mol.GetNumAtoms() == 0:
        return key, []
    safe = prepare_mol_for_pkasolver(mol)
    if safe is None:
        return key, []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            qm = QueryModel()
        with _discard_stdout_only(), isolated_sys_argv_for_embedded_cli():
            states = calculate_microstate_pka_values(safe, query_model=qm)
        pops = estimate_protomer_populations_from_states(states, ph)
        return key, [(smi, float(pct)) for smi, pct, _m in pops]
    except Exception:
        logger.exception("Protomer subprocess: prediction failed for key=%s", key[:48])
        return key, []


def estimate_protomer_populations_from_states(states, pH: float) -> list[tuple[str, float, Chem.Mol]]:
    """
    Approximate protomer mole fractions at ``pH`` from pkasolver microstates.

    Each microstate is treated as an independent Henderson–Hasselbalch equilibrium between
    ``protonated_mol`` and ``deprotonated_mol``; contributions are summed per canonical SMILES
    and renormalized. This ignores coupling between sites and is only a rough guide.
    """
    from molmanager.pkasolver_descriptor_support import hydrate_microstates

    states = hydrate_microstates(states)
    if not states:
        return []
    key_to_mol: dict[str, Chem.Mol] = {}
    acc: defaultdict[str, float] = defaultdict(float)
    for s in states:
        pka = float(s.pka)
        pm = s.protonated_mol
        dm = s.deprotonated_mol
        if pm is None or dm is None:
            continue
        sp = mol_to_canonical_smiles(pm)
        sd = mol_to_canonical_smiles(dm)
        if not sp or not sd:
            continue
        if sp not in key_to_mol:
            key_to_mol[sp] = Chem.Mol(pm)
        if sd not in key_to_mol:
            key_to_mol[sd] = Chem.Mol(dm)
        # Acid dissociation HA ⇌ H⁺ + A⁻ with macro/micro pKa: fraction A⁻ = 1 / (1 + 10^(pKa − pH))
        frac_deprot = 1.0 / (1.0 + 10.0 ** (pka - pH))
        frac_prot = 1.0 - frac_deprot
        acc[sp] += frac_prot
        acc[sd] += frac_deprot
    total = sum(acc.values())
    if total <= 0:
        ref = states[0].ph7_mol
        if ref is None:
            return []
        smi = mol_to_canonical_smiles(ref)
        if not smi:
            return []
        return [(smi, 100.0, Chem.Mol(ref))]
    out: list[tuple[str, float, Chem.Mol]] = []
    for k, v in acc.items():
        mol = key_to_mol.get(k)
        if mol is None:
            continue
        out.append((k, 100.0 * v / total, mol))
    out.sort(key=lambda t: -t[1])
    return out


class ProtomerGeneratorSignals(QObject):
    finished = pyqtSignal(list)  # list[tuple[int | None, str, float]]  source_oid, smiles, pct
    failed = pyqtSignal(str)


class ProtomerGeneratorWorker(QRunnable):
    """Enumerate protomers per input molecule and estimate populations at a target pH."""

    def __init__(
        self,
        rows: list[tuple[int | None, Chem.Mol | None]],
        pH: float,
        worker_signals,
        protomer_signals: ProtomerGeneratorSignals,
        cancel_event: threading.Event | None = None,
    ):
        super().__init__()
        self.rows = rows
        self.pH = float(pH)
        self.worker_signals = worker_signals
        self.protomer_signals = protomer_signals
        self.cancel_event = cancel_event

    def run(self) -> None:
        from . import pka_predictor as pka_mod

        with _quieter_pkasolver_dependency_loggers():
            try:
                _ensure_cairosvg_importable()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    _patch_pkasolver_dimorphite()
                    from pkasolver.query import QueryModel, calculate_microstate_pka_values
            except Exception as e:
                logger.exception("Protomer generator: failed to import pkasolver stack")
                _safe_emit(
                    self.protomer_signals,
                    "failed",
                    "Could not load pkasolver (missing PyTorch / torch-geometric / pkasolver?). "
                    f"Details: {e}",
                )
                return

            cancel_ev = self.cancel_event
            combined: list[tuple[int | None, str, float]] = []

            order, rep, oids_map = group_rows_by_structure(self.rows)
            n_work = sum(len(oids_map[k]) for k in order)
            tot = max(n_work, 1)
            n_unique = len(order)

            from ..config import load_config
            from .pkasolver_parallel import plan_pkasolver_process_workers

            use_mp, proc_workers = plan_pkasolver_process_workers(
                n_unique, load_config().protomer_process_workers
            )

            done_cum = 0
            prog_last = 0.0
            cancelled = False

            def _emit(done: int, *, force: bool = False) -> None:
                nonlocal prog_last
                now = time.monotonic()
                if force or done >= tot or (now - prog_last) >= 0.12:
                    prog_last = now
                    try:
                        self.worker_signals.tool_progress.emit("Generate protomers…", min(done, tot), tot)
                    except Exception:
                        pass

            _emit(0, force=True)

            if not order:
                _emit(tot, force=True)
                _safe_emit(self.protomer_signals, "finished", combined)
                return

            if use_mp:
                tasks = [(k, rep[k].ToBinary(), self.pH) for k in order]
                results_by_key: dict[str, list[tuple[str, float]]] = {}
                user_cancelled = False
                ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
                try:
                    pending = {ex.submit(_mp_compute_protomer_smiles_pct, t) for t in tasks}
                    while pending:
                        if should_terminate_process_pool(cancel_ev):
                            user_cancelled = True
                            cancelled = True
                            for f in pending:
                                f.cancel()
                            break
                        completed, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                        for f in completed:
                            if f.cancelled():
                                continue
                            try:
                                key, pops = f.result()
                                results_by_key[key] = pops
                                done_cum += len(oids_map.get(key, ()))
                            except Exception:
                                logger.exception("Protomer process-pool task failed")
                            _emit(done_cum)
                finally:
                    shutdown_process_pool_executor(
                        ex, kill_workers=should_terminate_process_pool(cancel_ev)
                    )
                for key in order:
                    pops = results_by_key.get(key, [])
                    for oid in oids_map.get(key, ()):
                        for smi, pct in pops:
                            combined.append((oid, smi, pct))
            else:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", FutureWarning)
                        with pka_mod._query_model_lock:
                            if pka_mod._query_model_singleton is None:
                                pka_mod._query_model_singleton = QueryModel()
                            qm = pka_mod._query_model_singleton
                except Exception as e:
                    logger.exception("Protomer generator: model load failed")
                    _safe_emit(self.protomer_signals, "failed", f"Could not load pkasolver neural models: {e}")
                    return

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    for key in order:
                        if cancel_ev is not None and cancel_ev.is_set():
                            cancelled = True
                            break
                        mol = rep[key]
                        safe_mol = prepare_mol_for_pkasolver(mol)
                        if safe_mol is None:
                            continue
                        try:
                            with _discard_stdout_only(), isolated_sys_argv_for_embedded_cli():
                                with pka_mod._query_model_lock:
                                    states = calculate_microstate_pka_values(safe_mol, query_model=qm)
                            pops = estimate_protomer_populations_from_states(states, self.pH)
                            for oid in oids_map[key]:
                                for smi, pct, _m in pops:
                                    combined.append((oid, smi, pct))
                        except Exception as e:
                            oids_here = oids_map[key]
                            logger.warning(
                                "Protomer generation failed for %s row(s) (structure key prefix %.40s…): %s",
                                len(oids_here),
                                key,
                                e,
                            )
                        done_cum += len(oids_map[key])
                        _emit(done_cum)

            _emit(tot, force=True)
            if cancelled and done_cum > 0:
                try:
                    self.worker_signals.partial_results.emit("Generate protomers", done_cum, tot)
                except Exception:
                    pass
            if use_mp:
                logger.debug(
                    "Protomer: %s table row(s), %s unique structure(s), process pool=%s",
                    n_work,
                    n_unique,
                    proc_workers,
                )
            else:
                logger.debug(
                    "Protomer: %s table row(s), %s unique structure(s), sequential (set "
                    "MOLMANAGER_PROTOmer_PROCESSES>1 to allow parallel workers when unique≥2)",
                    n_work,
                    n_unique,
                )
            _safe_emit(self.protomer_signals, "finished", combined)
