"""Descriptor, conformer, and custom calculator workers.

Fingerprint columns use RDKit implementations. The **2D pharmacophore (Gobbi)** on-bits column uses
``rdkit.Chem.Pharm2D`` with ``Gobbi_Pharm2D`` (Gobbi & Poppinger, *Perspect. Drug Discov. Des.* 1998).
Drug-likeness columns that invoke ``medchem_descriptors`` / **pkasolver** cite
``molmanager.science_citations`` and the worker module docstrings there.
"""

import logging
import math
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait

from .process_pool_utils import (
    register_process_pool,
    should_terminate_process_pool,
    shutdown_process_pool_executor,
)
from dataclasses import dataclass

from PyQt5.QtCore import QRunnable
from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors, QED, rdMolAlign, rdMolDescriptors

try:
    from rdkit.Chem.Pharm2D import Generate as Pharm2DGenerate
    from rdkit.Chem.Pharm2D import Gobbi_Pharm2D
except ImportError:  # pragma: no cover - very old RDKit builds
    Pharm2DGenerate = None  # type: ignore[misc, assignment]
else:

    def _pharm2d_gobbi_onbits(mol: Chem.Mol) -> int:
        fp = Pharm2DGenerate.Gen2DFingerprint(mol, Gobbi_Pharm2D.factory)
        if hasattr(fp, "GetNumOnBits"):
            return int(fp.GetNumOnBits())
        return int(sum(fp))

from ..config import load_config
from ..confs_codec import format_confs_table_cell, mol_from_packed_confs_cell, pack_confs_cell
from ..medchem_descriptors import (
    ab_mps_score,
    cns_mpo_score,
    esol_logS_intrinsic,
    lipinski_violations,
    logd74_value,
    logs74_value,
    mol_formula,
    mol_inchi_key,
    mol_net_formal_charge,
    ro5_pass,
)
from ..pkasolver_descriptor_support import int_fns_need_pkasolver, microstates_for_mol
from .pkasolver_parallel import build_microstates_cache_for_rows
from ..safe_calc import eval_custom_calc_expression
from ..utils import mol_to_canonical_smiles, parse_molecule_from_cell_text
from ..rdkit_fingerprints import spec_for_internal_key, fingerprint_onbits_for_internal_key
from .signals import WorkerSignals, emit_partial_results_if_cancelled

logger = logging.getLogger(__name__)


def _emit_tool_progress_throttled(
    signals: WorkerSignals,
    message: str,
    done: int,
    tot: int,
    state: list,
    *,
    progress_state=None,
) -> None:
    """Limit ``tool_progress`` emissions; always refresh ``ToolProgressState`` when provided."""
    from ..tool_progress import report_tool_progress

    report_tool_progress(
        message=message,
        done=done,
        total=tot,
        progress_state=progress_state,
        signals=signals,
        throttle=state,
    )


def _descriptor_int_fns_include_pharm2d(int_fns) -> bool:
    """2D pharmacophore (Gobbi) is much slower than other descriptor columns — tune parallelism."""
    return any(isinstance(f, str) and f.startswith("FP_Pharm2D") for f in int_fns)


def descriptor_callable_for_int_fn(i_f, smarts_cache, row_ctx=None):
    """Return ``callable(mol)`` for one internal descriptor id (shared by thread and process workers)."""
    ctx = row_ctx if row_ctx is not None else {}
    if i_f == "SMILES":
        return lambda m: mol_to_canonical_smiles(m) if m is not None else ""
    if i_f == "INCHIKEY":
        return lambda m: mol_inchi_key(m) if m is not None else ""
    if i_f == "MOLFORMULA":
        return lambda m: mol_formula(m) if m is not None else ""
    if i_f == "RO5_VIOLATIONS":
        return lambda m: lipinski_violations(m) if m is not None else 0
    if i_f == "RO5_PASS":
        return lambda m: ro5_pass(m) if m is not None else "No"
    if i_f == "LOGD74":
        return lambda m: logd74_value(m, ctx.get("pkasolver_states"))
    if i_f == "LOGS_ESOL":
        return lambda m: esol_logS_intrinsic(m) if m is not None else 0.0
    if i_f == "LOGS74":
        return lambda m: logs74_value(m, ctx.get("pkasolver_states"))
    if i_f == "AB_MPS":
        return lambda m: ab_mps_score(m, ctx.get("pkasolver_states")) if m is not None else 0.0
    if i_f == "CNS_MPO":
        return lambda m: cns_mpo_score(m, ctx.get("pkasolver_states")) if m is not None else 0.0
    if i_f == "QED":
        return lambda m: QED.qed(m)
    if i_f == "NET_FORMAL_CHARGE":
        return lambda m: mol_net_formal_charge(m) if m is not None else 0
    if i_f.startswith("Count_"):
        atom = i_f.split("_", 1)[1]
        s = Chem.MolFromSmarts(f"[{atom}]")
        smarts_cache[i_f] = s
        return lambda m, s=s: len(m.GetSubstructMatches(s))
    func = getattr(Descriptors, i_f, None)
    if func:
        return lambda m, f=func: f(m)
    func = getattr(rdMolDescriptors, i_f, None)
    if func:
        return lambda m, f=func: f(m)
    func = getattr(Chem, i_f, None)
    if func:
        return lambda m, f=func: f(m)
    if isinstance(i_f, str) and i_f.startswith("FP_"):
        if spec_for_internal_key(i_f) is not None:
            return fingerprint_onbits_for_internal_key(i_f)
    return lambda m: "N/A"


def _mp_calc_descriptor_row(args: tuple):
    """One row in a child process — avoids GIL contention with the Qt GUI thread."""
    idx, mol_bytes, disp_headers, int_fns, pka_states, pka_cache_used = args
    smarts_cache = {}
    row_ctx: dict = {}
    mol = None
    if mol_bytes:
        try:
            mol = Chem.Mol(mol_bytes)
        except Exception:
            mol = None
    if mol is not None and int_fns_need_pkasolver(int_fns):
        if pka_cache_used:
            row_ctx["pkasolver_states"] = pka_states
        else:
            row_ctx["pkasolver_states"] = microstates_for_mol(mol)
    callables = [descriptor_callable_for_int_fn(i_f, smarts_cache, row_ctx) for i_f in int_fns]
    row_data: dict[str, str] = {}
    if mol:
        for d_n, fn in zip(disp_headers, callables):
            try:
                v = fn(mol)
                row_data[d_n] = f"{v:.3f}" if isinstance(v, float) else str(v)
            except Exception:
                row_data[d_n] = "N/A"
    else:
        for d_n in disp_headers:
            row_data[d_n] = "N/A"
    return (idx, row_data)


def _descriptor_progress_emit_step(total: int) -> int:
    """Fewer cross-thread progress signals on very large tables."""
    tot = max(1, int(total))
    if tot >= 100_000:
        return max(1, tot // 200)
    if tot >= 10_000:
        return max(1, tot // 80)
    return max(1, tot // 40)


def _run_descriptor_process_pool(
    prepared: list,
    *,
    disp_headers: list,
    int_fns: tuple,
    pka_by_idx: dict,
    pka_cache_used: bool,
    max_workers: int,
    cancel_event: threading.Event | None,
    emit_progress,
) -> tuple[list, bool]:
    """
    Run descriptor rows in child processes (keeps the Qt GUI thread off the GIL).

    Returns ``(results, cancelled)``.
    """
    mp_tasks = []
    for i, mol in prepared:
        blob = mol.ToBinary() if mol is not None else b""
        mp_tasks.append(
            (
                i,
                blob,
                tuple(disp_headers),
                tuple(int_fns),
                pka_by_idx.get(i) if pka_cache_used else None,
                pka_cache_used,
            )
        )
    proc_workers = min(max_workers, max(2, (os.cpu_count() or 4) - 1), 8)
    emit_progress(0, force=True)
    mp_results_dict: dict = {}
    done_count = 0
    cancelled = False
    last_pulse = 0.0
    ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
    try:
        pending = {ex.submit(_mp_calc_descriptor_row, t) for t in mp_tasks}
        while pending:
            if should_terminate_process_pool(cancel_event):
                cancelled = True
                for f in list(pending):
                    if f.done() and not f.cancelled():
                        try:
                            idx, row_d = f.result()
                            mp_results_dict[int(idx)] = row_d
                            done_count += 1
                        except Exception:
                            logger.exception("Process-pool descriptor row failed")
                    else:
                        f.cancel()
                break
            completed, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            if not completed and pending:
                now = time.monotonic()
                if now - last_pulse >= 0.55:
                    last_pulse = now
                    emit_progress(done_count, force=True)
            for f in completed:
                if f.cancelled():
                    continue
                try:
                    idx, row_d = f.result()
                    mp_results_dict[int(idx)] = row_d
                    done_count += 1
                    emit_progress(done_count)
                except Exception:
                    logger.exception("Process-pool descriptor row failed")
    finally:
        shutdown_process_pool_executor(
            ex, kill_workers=should_terminate_process_pool(cancel_event)
        )
    emit_progress(done_count, force=True)
    results = [(oid, mp_results_dict[oid]) for oid, _ in prepared if oid in mp_results_dict]
    return results, cancelled


def _calc_descriptor_row_task(args):
    """One row for :class:`CalcWorker` parallel path (thread worker)."""
    idx, mol, disp_headers, int_fns, smarts_cache, pka_states, pka_cache_used = args
    if mol is not None:
        try:
            mol = Chem.Mol(mol)
        except Exception:
            mol = None
    row_ctx: dict = {}
    if mol is not None and int_fns_need_pkasolver(int_fns):
        if pka_cache_used:
            row_ctx["pkasolver_states"] = pka_states
        else:
            row_ctx["pkasolver_states"] = microstates_for_mol(mol)
    callables = [descriptor_callable_for_int_fn(i_f, smarts_cache, row_ctx) for i_f in int_fns]
    row_data: dict[str, str] = {}
    if mol:
        for d_n, fn in zip(disp_headers, callables):
            try:
                v = fn(mol)
                row_data[d_n] = f"{v:.3f}" if isinstance(v, float) else str(v)
            except Exception:
                row_data[d_n] = "N/A"
    else:
        for d_n in disp_headers:
            row_data[d_n] = "N/A"
    return (idx, row_data)


# --- Conformer generation (Tools → Generate Conformations) -----------------


@dataclass(frozen=True)
class ConformerGenParams:
    """Options for :func:`run_conformer_generation` / :class:`ConformerGenerationWorker`."""

    num_confs: int = 10
    energy_window_kcal: float = 10.0
    force_field: str = "MMFF"
    random_seed: int = 0xC0FFEE
    prune_rms_threshold: float = -1.0
    max_iterations: int = 200

    @classmethod
    def single_lowest_energy(
        cls,
        *,
        force_field: str = "MMFF",
        random_seed: int = 0xC0FFEE,
        prune_rms_threshold: float = -1.0,
        max_iterations: int = 200,
    ) -> "ConformerGenParams":
        """One embedded conformer, minimized; written to the ``confs`` column."""
        return cls(
            num_confs=1,
            energy_window_kcal=0.0,
            force_field=force_field,
            random_seed=random_seed,
            prune_rms_threshold=prune_rms_threshold,
            max_iterations=max_iterations,
        )


def _etkdg_params(random_seed: int, prune_rms_threshold: float):
    for name in ("ETKDGv3", "ETKDGv2", "ETKDG"):
        factory = getattr(AllChem, name, None)
        if factory is None:
            continue
        try:
            p = factory()
            p.randomSeed = int(random_seed)
            if prune_rms_threshold is not None and prune_rms_threshold >= 0:
                p.pruneRmsThresh = float(prune_rms_threshold)
            return p
        except Exception:
            continue
    return None


def _optimize_conformer_energies_cooperative(
    m: Chem.Mol,
    params: ConformerGenParams,
    meta: dict,
    cancel_event: threading.Event,
    max_it: int,
) -> tuple[list[float], str] | None:
    """Per-conformer minimization so ``cancel_event`` can abort between conformers."""
    ff_choice = (params.force_field or "MMFF").strip().upper()
    nconf = m.GetNumConformers()
    energies: list[float] = []
    if ff_choice == "MMFF":
        mp = AllChem.MMFFGetMoleculeProperties(m)
        if mp is not None:
            for cid in range(nconf):
                if cancel_event.is_set():
                    meta["err"] = "cancelled"
                    return None
                code = AllChem.MMFFOptimizeMolecule(m, confId=cid, maxIters=max_it)
                if code == -1:
                    meta["err"] = "mmff_opt"
                    return None
                ff = AllChem.MMFFGetMoleculeForceField(m, mp, confId=cid)
                if ff is None:
                    meta["err"] = "mmff_ff"
                    return None
                energies.append(float(ff.CalcEnergy()))
            return energies, "MMFF"
    for cid in range(nconf):
        if cancel_event.is_set():
            meta["err"] = "cancelled"
            return None
        code = AllChem.UFFOptimizeMolecule(m, confId=cid, maxIters=max_it)
        if code == -1:
            meta["err"] = "uff_opt"
            return None
        ff = AllChem.UFFGetMoleculeForceField(m, confId=cid)
        energies.append(float(ff.CalcEnergy()))
    return energies, "UFF"


def _optimize_conformer_energies_batch(
    m: Chem.Mol, params: ConformerGenParams, meta: dict, max_it: int
) -> tuple[list[float], str] | None:
    """Fast path: RDKit batch optimizers (no cooperative cancel during minimization)."""
    ff = (params.force_field or "MMFF").strip().upper()
    res = None
    try:
        if ff == "MMFF":
            mp = AllChem.MMFFGetMoleculeProperties(m)
            if mp is None:
                ff = "UFF"
            else:
                res = AllChem.MMFFOptimizeMoleculeConfs(m, numThreads=1, maxIters=max_it)
        if ff == "UFF" or res is None:
            res = AllChem.UFFOptimizeMoleculeConfs(m, maxIters=max_it)
            ff = "UFF"
    except Exception as e:
        meta["err"] = f"minimize:{e.__class__.__name__}"
        return None
    return [float(t[1]) for t in res], ff


def run_conformer_generation(
    mol: Chem.Mol,
    params: ConformerGenParams,
    cancel_event: threading.Event | None = None,
) -> tuple[Chem.Mol | None, dict]:
    """
    Embed multiple conformers, minimize (MMFF or UFF), prune by energy window, RemoveHs.

    Returns ``(mol_or_None, meta)``. The UI writes a ``confs`` cell via :func:`~molmanager.confs_codec.pack_confs_cell`
    (metadata plus packed mol blocks when there are multiple conformers) and does **not** replace the row's
    working molecule or redraw the Structure column.

    When ``cancel_event`` is set, minimization checks it between conformers (slower than the batch
    optimizers used when ``cancel_event`` is None). Embed is still a single RDKit call.

    For very large ensembles or many rows, packing may truncate conformers to fit the cell size limit;
    consider storing only a path or DB key in ``confs`` and keeping payloads on disk instead.
    """
    meta: dict = {"ok": False, "n_requested": int(params.num_confs), "seed": int(params.random_seed)}
    if cancel_event is not None and cancel_event.is_set():
        meta["err"] = "cancelled"
        return None, meta
    if mol is None or mol.GetNumAtoms() == 0:
        meta["err"] = "empty_molecule"
        return None, meta

    try:
        m = Chem.AddHs(Chem.Mol(mol), addCoords=True)
    except Exception as e:
        meta["err"] = f"addhs:{e.__class__.__name__}"
        return None, meta

    if cancel_event is not None and cancel_event.is_set():
        meta["err"] = "cancelled"
        return None, meta

    embed_params = _etkdg_params(params.random_seed, params.prune_rms_threshold)
    if embed_params is None:
        meta["err"] = "no_etkdg"
        return None, meta

    try:
        cids = AllChem.EmbedMultipleConfs(m, int(params.num_confs), embed_params)
        n_embed = len(cids) if cids is not None else 0
    except Exception as e:
        meta["err"] = f"embed:{e.__class__.__name__}"
        return None, meta

    meta["n_embedded"] = int(n_embed)
    if n_embed == 0 or m.GetNumConformers() == 0:
        meta["err"] = "no_embedded_confs"
        return None, meta

    if cancel_event is not None and cancel_event.is_set():
        meta["err"] = "cancelled"
        return None, meta

    max_it = max(1, int(params.max_iterations))
    if cancel_event is None:
        opt = _optimize_conformer_energies_batch(m, params, meta, max_it)
    else:
        opt = _optimize_conformer_energies_cooperative(m, params, meta, cancel_event, max_it)
    if opt is None:
        return None, meta
    energies, ff = opt
    meta["ff"] = ff
    emin = min(energies)
    meta["e_min_kcal"] = round(emin, 4)
    window = float(params.energy_window_kcal)
    meta["ewin_kcal"] = round(window, 4) if window > 0 else 0.0
    if window > 0:
        keep = {i for i, e in enumerate(energies) if e <= emin + window}
    else:
        keep = set(range(len(energies)))
    kept_energies = [energies[i] for i in range(len(energies)) if i in keep]
    meta["e_max_kept_kcal"] = round(max(kept_energies), 4) if kept_energies else None
    meta["n_kept"] = len(keep)

    for cid in sorted(set(range(m.GetNumConformers())) - keep, reverse=True):
        try:
            m.RemoveConformer(int(cid))
        except Exception:
            pass

    try:
        m = Chem.RemoveHs(m)
    except Exception:
        pass

    meta["ok"] = True
    return m, meta


def _conformer_row_task(task: tuple) -> tuple[int, Chem.Mol | None, str]:
    oid, mol, params = task[0], task[1], task[2]
    cancel_event = task[3] if len(task) > 3 else None
    try:
        if mol is None:
            meta = {
                "ok": False,
                "err": "missing_mol",
                "n_requested": int(params.num_confs),
                "seed": int(params.random_seed),
            }
            return oid, None, format_confs_table_cell(meta)
        new_m, meta = run_conformer_generation(mol, params, cancel_event=cancel_event)
        return oid, new_m, pack_confs_cell(meta, new_m)
    except Exception as e:
        logger.exception("ConformerGenerationWorker failed for oid=%s", oid)
        meta = {
            "ok": False,
            "err": str(e)[:200],
            "n_requested": int(params.num_confs),
            "seed": int(params.random_seed),
        }
        return oid, None, format_confs_table_cell(meta)


class ConformerGenerationWorker(QRunnable):
    """Run :func:`run_conformer_generation` off the UI thread (optionally parallel per row)."""

    def __init__(
        self,
        data: list[tuple[int, Chem.Mol | None]],
        params: ConformerGenParams,
        signals: WorkerSignals,
        cancel_event: threading.Event | None = None,
        progress_state=None,
    ):
        super().__init__()
        self.data = data
        self.params = params
        self.signals = signals
        self.cancel_event = cancel_event
        self.progress_state = progress_state

    def run(self):
        nrows = len(self.data)
        tot = max(nrows, 1)
        tasks = [(oid, mol, self.params) for oid, mol in self.data]
        cfg = load_config()
        if cfg.conformer_threads is not None:
            max_workers = cfg.conformer_threads
        else:
            max_workers = min(4, max(1, (os.cpu_count() or 4) // 2))

        use_parallel = nrows >= 6 and max_workers > 1
        cancel_ev = self.cancel_event
        results: list = []
        cancelled = False
        done_count = 0
        prog_state = [0, 0.0]
        try:
            if use_parallel:
                _emit_tool_progress_throttled(
                    self.signals,
                    "Generate conformations…",
                    0,
                    tot,
                    prog_state,
                    progress_state=self.progress_state,
                )
                ex = ThreadPoolExecutor(max_workers=max_workers)
                shutdown_cancel = False
                try:
                    row_tasks = [(*t, cancel_ev) for t in tasks]
                    pending = {ex.submit(_conformer_row_task, rt) for rt in row_tasks}
                    done_count = 0
                    while pending:
                        if cancel_ev is not None and cancel_ev.is_set():
                            shutdown_cancel = True
                            cancelled = True
                            for f in list(pending):
                                if f.done() and not f.cancelled():
                                    try:
                                        results.append(f.result())
                                        done_count += 1
                                    except Exception:
                                        logger.exception("Conformer row task failed")
                                else:
                                    f.cancel()
                            break
                        completed, pending = wait(pending, timeout=0.08, return_when=FIRST_COMPLETED)
                        for f in completed:
                            if f.cancelled():
                                continue
                            try:
                                results.append(f.result())
                                done_count += 1
                            except Exception:
                                logger.exception("Conformer row task failed")
                            _emit_tool_progress_throttled(
                                self.signals,
                                "Generate conformations…",
                                done_count,
                                tot,
                                prog_state,
                                progress_state=self.progress_state,
                            )
                finally:
                    try:
                        ex.shutdown(wait=not shutdown_cancel, cancel_futures=shutdown_cancel)
                    except TypeError:
                        ex.shutdown(wait=not shutdown_cancel)
                _emit_tool_progress_throttled(
                    self.signals,
                    "Generate conformations…",
                    min(done_count, tot),
                    tot,
                    prog_state,
                    progress_state=self.progress_state,
                )
            else:
                for done, t in enumerate(tasks, start=1):
                    if cancel_ev is not None and cancel_ev.is_set():
                        cancelled = True
                        break
                    results.append(_conformer_row_task((*t, cancel_ev)))
                    done_count = done
                    _emit_tool_progress_throttled(
                        self.signals,
                        "Generate conformations…",
                        done,
                        tot,
                        prog_state,
                        progress_state=self.progress_state,
                    )
        finally:
            emit_partial_results_if_cancelled(
                self.signals, "Generate conformations", done_count, tot, cancelled
            )
            try:
                self.signals.conformers_finished.emit(results)
            except Exception:
                logger.warning("conformers_finished emit failed", exc_info=True)


@dataclass(frozen=True)
class SuperposeParams:
    """Options for :func:`run_superpose_conformers` / :class:`SuperposeConformersWorker`."""

    reference_conformer_index: int = 0
    heavy_atoms_only: bool = True
    reflect: bool = False
    max_align_iters: int = 50
    # When non-empty, RMS alignment uses only atoms matching this pattern (SMILES or SMARTS).
    align_pattern: str = ""
    align_pattern_is_smarts: bool = False


def _superpose_atom_map(m: Chem.Mol, params: SuperposeParams) -> tuple[list[tuple[int, int]] | None, str | None]:
    """
    Build ``atomMap`` for :func:`rdMolAlign.AlignMol` (probe index, ref index) for same-molecule conformers.

    Returns ``(atom_map, None)`` or ``(None, error_code)``.
    """
    pat = (params.align_pattern or "").strip()
    if not pat:
        if params.heavy_atoms_only:
            am = [(i, i) for i in range(m.GetNumAtoms()) if m.GetAtomWithIdx(i).GetAtomicNum() != 1]
        else:
            am = [(i, i) for i in range(m.GetNumAtoms())]
        if len(am) < 2:
            return None, "too_few_atoms_for_alignment"
        return am, None
    q: Chem.Mol | None
    try:
        if params.align_pattern_is_smarts:
            q = Chem.MolFromSmarts(pat)
        else:
            q = Chem.MolFromSmiles(pat)
    except Exception:
        q = None
    if q is None:
        return None, "invalid_align_pattern"
    try:
        match = m.GetSubstructMatch(q)
    except Exception:
        return None, "substructure_match_failed"
    if not match or len(match) < 1:
        return None, "align_pattern_not_found"
    idxs = [int(i) for i in match]
    if params.heavy_atoms_only:
        idxs = [i for i in idxs if m.GetAtomWithIdx(i).GetAtomicNum() != 1]
    if len(idxs) < 2:
        return None, "too_few_atoms_in_match"
    return [(i, i) for i in idxs], None


def run_superpose_conformers(
    mol: Chem.Mol,
    params: SuperposeParams,
    cancel_event: threading.Event | None = None,
) -> tuple[Chem.Mol | None, dict]:
    """
    Superpose all conformers of *mol* onto one reference conformer using :func:`rdMolAlign.AlignMol`.

    Conformer coordinates in *mol* are updated in place on a copy of the input molecule.
    """
    meta: dict = {"ok": False, "op": "superpose"}
    try:
        m = Chem.Mol(mol)
    except Exception:
        meta["err"] = "bad_mol"
        return None, meta
    try:
        nconf = int(m.GetNumConformers())
    except Exception:
        nconf = 0
    if nconf < 2:
        meta["err"] = "need_at_least_two_conformers"
        return None, meta
    try:
        cids = sorted(c.GetId() for c in m.GetConformers())
    except Exception:
        cids = list(range(nconf))
    if not cids:
        meta["err"] = "no_conformer_ids"
        return None, meta
    ref_idx = int(params.reference_conformer_index)
    if ref_idx < 0:
        ref_idx = 0
    ref_clamped = False
    if ref_idx >= len(cids):
        ref_idx = len(cids) - 1
        ref_clamped = True
    ref_cid = int(cids[ref_idx])
    atom_map, map_err = _superpose_atom_map(m, params)
    if map_err or not atom_map:
        meta["err"] = map_err or "no_atoms_for_alignment"
        return None, meta
    rms_vals: list[float] = []
    max_it = max(10, int(params.max_align_iters))
    try:
        for cid in cids:
            if cancel_event is not None and cancel_event.is_set():
                meta["err"] = "cancelled"
                return None, meta
            ic = int(cid)
            if ic == ref_cid:
                rms_vals.append(0.0)
                continue
            rms = float(
                rdMolAlign.AlignMol(
                    m,
                    m,
                    prbCid=ic,
                    refCid=ref_cid,
                    atomMap=atom_map,
                    reflect=bool(params.reflect),
                    maxIters=max_it,
                )
            )
            rms_vals.append(rms)
    except Exception as e:
        logger.exception("run_superpose_conformers failed")
        meta["err"] = str(e)[:200]
        return None, meta
    meta["ok"] = True
    meta["ref_cid"] = ref_cid
    meta["ref_clamped"] = ref_clamped
    meta["n_conf"] = len(cids)
    meta["rms_mean"] = round(sum(rms_vals) / max(len(rms_vals), 1), 6)
    meta["rms_max"] = round(max(rms_vals), 6)
    meta["heavy"] = bool(params.heavy_atoms_only)
    meta["reflect"] = bool(params.reflect)
    meta["max_align_iters"] = max_it
    meta["n_align_atoms"] = len(atom_map)
    ap = (params.align_pattern or "").strip()
    if ap:
        meta["align_smarts"] = bool(params.align_pattern_is_smarts)
        meta["align_pattern"] = ap[:120]
    return m, meta


def _superpose_row_task(task: tuple) -> tuple[int, Chem.Mol | None, str]:
    oid, cell, params = task[0], task[1], task[2]
    cancel_event = task[3] if len(task) > 3 else None
    try:
        if cancel_event is not None and cancel_event.is_set():
            return oid, None, format_confs_table_cell({"ok": False, "err": "cancelled", "op": "superpose"})
        mol = mol_from_packed_confs_cell(cell or "")
        if mol is None:
            return oid, None, format_confs_table_cell({"ok": False, "err": "no_packed_conformers", "op": "superpose"})
        new_m, meta = run_superpose_conformers(mol, params, cancel_event=cancel_event)
        if new_m is None:
            return oid, None, format_confs_table_cell(meta)
        return oid, new_m, pack_confs_cell(meta, new_m)
    except Exception as e:
        logger.exception("SuperposeConformersWorker failed for oid=%s", oid)
        return oid, None, format_confs_table_cell({"ok": False, "err": str(e)[:200], "op": "superpose"})


class SuperposeConformersWorker(QRunnable):
    """Align conformers from packed ``confs`` cells into a new ``superpose`` column payload."""

    def __init__(
        self,
        data: list[tuple[int, str]],
        params: SuperposeParams,
        signals: WorkerSignals,
        cancel_event: threading.Event | None = None,
        progress_state=None,
    ):
        super().__init__()
        self.data = data
        self.params = params
        self.signals = signals
        self.cancel_event = cancel_event
        self.progress_state = progress_state

    def run(self):
        nrows = len(self.data)
        tot = max(nrows, 1)
        tasks = [(oid, cell, self.params) for oid, cell in self.data]
        cfg = load_config()
        if cfg.conformer_threads is not None:
            max_workers = cfg.conformer_threads
        else:
            max_workers = min(4, max(1, (os.cpu_count() or 4) // 2))
        use_parallel = nrows >= 6 and max_workers > 1
        cancel_ev = self.cancel_event
        results: list = []
        cancelled = False
        done_count = 0
        prog_state = [0, 0.0]
        try:
            if use_parallel:
                _emit_tool_progress_throttled(
                    self.signals,
                    "Superpose conformers…",
                    0,
                    tot,
                    prog_state,
                    progress_state=self.progress_state,
                )
                ex = ThreadPoolExecutor(max_workers=max_workers)
                shutdown_cancel = False
                try:
                    row_tasks = [(*t, cancel_ev) for t in tasks]
                    pending = {ex.submit(_superpose_row_task, rt) for rt in row_tasks}
                    done_count = 0
                    while pending:
                        if cancel_ev is not None and cancel_ev.is_set():
                            shutdown_cancel = True
                            cancelled = True
                            for f in list(pending):
                                if f.done() and not f.cancelled():
                                    try:
                                        results.append(f.result())
                                        done_count += 1
                                    except Exception:
                                        logger.exception("Superpose row task failed")
                                else:
                                    f.cancel()
                            break
                        completed, pending = wait(pending, timeout=0.08, return_when=FIRST_COMPLETED)
                        for f in completed:
                            if f.cancelled():
                                continue
                            try:
                                results.append(f.result())
                                done_count += 1
                            except Exception:
                                logger.exception("Superpose row task failed")
                            _emit_tool_progress_throttled(
                                self.signals,
                                "Superpose conformers…",
                                done_count,
                                tot,
                                prog_state,
                                progress_state=self.progress_state,
                            )
                finally:
                    try:
                        ex.shutdown(wait=not shutdown_cancel, cancel_futures=shutdown_cancel)
                    except TypeError:
                        ex.shutdown(wait=not shutdown_cancel)
                _emit_tool_progress_throttled(
                    self.signals,
                    "Superpose conformers…",
                    min(done_count, tot),
                    tot,
                    prog_state,
                    progress_state=self.progress_state,
                )
            else:
                for done, t in enumerate(tasks, start=1):
                    if cancel_ev is not None and cancel_ev.is_set():
                        cancelled = True
                        break
                    results.append(_superpose_row_task((*t, cancel_ev)))
                    done_count = done
                    _emit_tool_progress_throttled(
                        self.signals,
                        "Superpose conformers…",
                        done,
                        tot,
                        prog_state,
                        progress_state=self.progress_state,
                    )
        finally:
            emit_partial_results_if_cancelled(
                self.signals, "Superpose conformers", done_count, tot, cancelled
            )
            try:
                self.signals.superpose_finished.emit(results)
            except Exception:
                logger.warning("superpose_finished emit failed", exc_info=True)


class CalcWorker(QRunnable):
    def __init__(
        self,
        data,
        disp_headers,
        int_fns,
        is_smiles,
        signals,
        cancel_event: threading.Event | None = None,
        progress_state=None,
    ):
        super().__init__()
        self.data, self.disp_headers, self.int_fns, self.is_smiles, self.signals = (
            data,
            disp_headers,
            int_fns,
            is_smiles,
            signals,
        )
        self.cancel_event = cancel_event
        self.progress_state = progress_state

    def run(self):
        smarts_cache = {}
        nrows = len(self.data)
        tot = max(nrows, 1)
        prepared = []
        prep_emit_step = max(1, nrows // 80)
        prep_last_emit = 0.0

        def _emit_prep_progress(done_count: int, *, force: bool = False) -> None:
            nonlocal prep_last_emit
            from ..tool_progress import report_tool_progress

            now = time.monotonic()
            if force or done_count >= nrows or (now - prep_last_emit) >= 0.2:
                prep_last_emit = now
                report_tool_progress(
                    message="Preparing descriptors…",
                    done=done_count,
                    total=tot,
                    progress_state=self.progress_state,
                    signals=self.signals,
                    force_signal=force,
                )

        for idx, (i, item) in enumerate(self.data):
            if self.is_smiles:
                smi = item.strip() if isinstance(item, str) else ""
                mol = parse_molecule_from_cell_text(smi) if smi else None
            else:
                mol = item
            prepared.append((i, mol))
            if idx == 0 or idx + 1 >= nrows or (idx + 1) % prep_emit_step == 0:
                _emit_prep_progress(idx + 1)
        _emit_prep_progress(len(prepared), force=True)

        cfg = load_config()
        if cfg.descriptor_threads is not None:
            max_workers = cfg.descriptor_threads
        else:
            max_workers = min(8, max(1, (os.cpu_count() or 4)))

        pka_by_idx: dict[int, list | None] = {}
        pka_cache_used = False
        if int_fns_need_pkasolver(self.int_fns) and nrows > 0:
            pka_by_idx = build_microstates_cache_for_rows(
                prepared,
                cancel_event=self.cancel_event,
                progress_state=self.progress_state,
                progress_message="pkasolver microstates…",
            )
            pka_cache_used = True
            from ..tool_progress import report_tool_progress

            report_tool_progress(
                message="Calculate descriptors",
                done=0,
                total=tot,
                progress_state=self.progress_state,
                signals=self.signals,
                force_signal=True,
            )
        # ThreadPoolExecutor row tasks so RDKit never runs on the Qt GUI thread and small jobs
        # still use a worker thread instead of the process-queue thread doing every row inline.
        heavy_pharm2d = _descriptor_int_fns_include_pharm2d(self.int_fns)
        cancel_ev = self.cancel_event
        cancelled = False

        prog_last_emit = 0.0
        prog_last_done = -1

        def _emit_progress(done_count: int, *, force: bool = False) -> None:
            """Update shared progress state every row; throttle cross-thread signal emissions."""
            nonlocal prog_last_emit, prog_last_done
            from ..tool_progress import report_tool_progress

            now = time.monotonic()
            step = _descriptor_progress_emit_step(tot)
            throttle_signal = (
                force
                or done_count >= tot
                or done_count <= 1
                or (done_count - prog_last_done) >= step
                or (now - prog_last_emit) >= 0.15
            )
            if throttle_signal:
                prog_last_emit = now
                prog_last_done = done_count
            report_tool_progress(
                message="Calculate descriptors",
                done=done_count,
                total=tot,
                progress_state=self.progress_state,
                signals=self.signals if throttle_signal else None,
                force_signal=force,
            )

        # RDKit descriptor work holds the GIL; large tables use child processes so Qt stays responsive.
        mp_min = int(cfg.descriptor_process_pool_min_rows)
        use_process_pool = (
            (heavy_pharm2d or nrows >= mp_min)
            and nrows >= 2
            and max_workers > 1
        )
        mp_used = False
        if use_process_pool:
            try:
                results, pool_cancelled = _run_descriptor_process_pool(
                    prepared,
                    disp_headers=self.disp_headers,
                    int_fns=tuple(self.int_fns),
                    pka_by_idx=pka_by_idx,
                    pka_cache_used=pka_cache_used,
                    max_workers=max_workers,
                    cancel_event=cancel_ev,
                    emit_progress=_emit_progress,
                )
                cancelled = cancelled or pool_cancelled
                mp_used = True
            except Exception:
                logger.exception("Process-pool descriptors failed; falling back to in-process pool")
                mp_used = False

        if mp_used:
            pass
        elif nrows == 0:
            results = []
        else:
            _emit_progress(0, force=True)
            tasks = [
                (
                    i,
                    mol,
                    self.disp_headers,
                    tuple(self.int_fns),
                    smarts_cache,
                    pka_by_idx.get(i) if pka_cache_used else None,
                    pka_cache_used,
                )
                for i, mol in prepared
            ]
            results = []
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                pending = {ex.submit(_calc_descriptor_row_task, t) for t in tasks}
                done_count = 0
                last_pulse = 0.0
                while pending:
                    if cancel_ev is not None and cancel_ev.is_set():
                        cancelled = True
                        for f in pending:
                            f.cancel()
                        for f in list(pending):
                            if f.done():
                                try:
                                    results.append(f.result())
                                    done_count += 1
                                except Exception:
                                    pass
                                pending.discard(f)
                        break
                    completed, pending = wait(pending, timeout=0.12, return_when=FIRST_COMPLETED)
                    if not completed and pending:
                        now = time.monotonic()
                        if now - last_pulse >= 0.55:
                            last_pulse = now
                            _emit_progress(done_count, force=True)
                    for f in completed:
                        if f.cancelled():
                            continue
                        try:
                            results.append(f.result())
                            done_count += 1
                        except Exception:
                            logger.exception("Descriptor row task failed")
                        _emit_progress(done_count)
            _emit_progress(min(done_count, tot), force=True)

        emit_partial_results_if_cancelled(
            self.signals, "Calculate descriptors", len(results), tot, cancelled
        )
        self.signals.calculated.emit(results, self.disp_headers)


def describe_custom_calc_error(exc: BaseException) -> str:
    """Human-readable explanation for failed custom calculator evaluation."""
    if isinstance(exc, ZeroDivisionError):
        return "Division by zero (the denominator evaluates to zero)."
    if isinstance(exc, OverflowError):
        return "Numeric overflow (the result is too large to represent)."
    if isinstance(exc, ValueError):
        msg = str(exc).strip()
        if msg:
            return f"Invalid value: {msg}"
        return "Invalid value for this operation (for example, square root of a negative number)."
    if isinstance(exc, TypeError):
        msg = str(exc).strip()
        if msg:
            return f"Incompatible types: {msg}"
        return "Incompatible types for this operation."
    if isinstance(exc, NameError):
        name = getattr(exc, "name", None) or ""
        if name:
            return f'Unknown name "{name}" (only math helpers and column variables are allowed).'
        return f"Unknown name in expression: {exc}"
    if isinstance(exc, SyntaxError):
        msg = getattr(exc, "msg", None) or str(exc)
        return f"Invalid expression syntax: {msg}"
    if isinstance(exc, ArithmeticError):
        return f"Arithmetic error: {exc}"
    return f"Could not evaluate: {exc.__class__.__name__}: {exc}"


class CustomCalcWorker(QRunnable):
    """Evaluate a numeric expression per row via a restricted ``ast`` evaluator (or legacy ``eval``).

    Only ``math`` helpers and rewritten column variables are in scope. This is not a
    full sandbox—do not run sessions with untrusted expressions on sensitive machines.
    Set ``MOLMANAGER_CUSTOM_CALC_LEGACY_EVAL`` to restore the old ``eval`` path if needed.
    """

    def __init__(
        self,
        row_data,
        expression,
        signals,
        cancel_event: threading.Event | None = None,
        progress_state=None,
    ):
        super().__init__()
        self.row_data, self.expression, self.signals = row_data, expression, signals
        self.cancel_event = cancel_event
        self.progress_state = progress_state

    def run(self):
        results = []
        use_legacy_eval = load_config().custom_calc_legacy_eval
        expr_template = (self.expression or "").strip()
        # Support both bracketed refs ([MW]) and bare refs (MW).
        req_vars = re.findall(r"\\[(.*?)\\]", expr_template)
        math_scope = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
        rows = list(self.row_data)
        tot = max(len(rows), 1)
        cancelled = False
        prog_last_emit = 0.0
        prog_last_done = -1
        done = 0
        for done, (idx, data_map) in enumerate(rows, start=1):
            if self.cancel_event is not None and self.cancel_event.is_set():
                cancelled = True
                break
            try:
                expr = expr_template
                local_scope = dict(math_scope)

                # Build stable variable bindings and rewrite the expression to use them.
                # We avoid injecting raw numbers repeatedly so we can also support bare variable tokens.
                var_keys = list(data_map.keys()) if isinstance(data_map, dict) else []
                # Include bracketed-only variables even if missing from row map.
                for v in req_vars:
                    if v not in var_keys:
                        var_keys.append(v)

                for i, var in enumerate(var_keys):
                    safe_name = f"__v{i}"
                    raw = (data_map.get(var, 0) if isinstance(data_map, dict) else 0)
                    try:
                        val = float(str(raw).strip()) if str(raw).strip() != "" else 0.0
                    except Exception:
                        val = 0.0
                    local_scope[safe_name] = val
                    expr = expr.replace(f"[{var}]", safe_name)
                    # Replace bare tokens that match the variable name (word-boundary safe).
                    expr = re.sub(rf"\\b{re.escape(var)}\\b", safe_name, expr)

                # Common convenience: if expression is just a variable label, allow it.
                if not expr:
                    res = "Empty expression (nothing to evaluate)."
                else:
                    if use_legacy_eval:
                        res = eval(expr, {"__builtins__": None}, local_scope)
                    else:
                        res = eval_custom_calc_expression(expr, local_scope)
            except Exception as e:
                res = describe_custom_calc_error(e)
            results.append((idx, f"{res:.3f}" if isinstance(res, float) else str(res)))
            if self.progress_state is not None:
                self.progress_state.update("Calculator…", done, tot)
            now = time.monotonic()
            step = max(1, tot // 40)
            if (
                done <= 1
                or done >= tot
                or (done - prog_last_done) >= step
                or (now - prog_last_emit) >= 0.15
            ):
                prog_last_emit = now
                prog_last_done = done
                try:
                    self.signals.tool_progress.emit("Calculator…", done, tot)
                except Exception:
                    pass
        if self.progress_state is not None:
            self.progress_state.update("Calculator…", min(done, tot) if rows else 0, tot)
        emit_partial_results_if_cancelled(self.signals, "Calculator", len(results), tot, cancelled)
        self.signals.custom_calc.emit(results)

