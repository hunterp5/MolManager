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
from ..rdkit_fingerprints import (
    fingerprint_onbits_for_descriptor,
    fingerprint_onbits_for_internal_key,
    int_fns_include_fingerprints,
    spec_for_internal_key,
)
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


def _descriptor_process_pool_min_rows(cfg, int_fns) -> int:
    """Row-count threshold for child-process descriptor calculation."""
    if _descriptor_int_fns_include_pharm2d(int_fns):
        return 2
    if int_fns_include_fingerprints(int_fns):
        return int(cfg.descriptor_fp_process_pool_min_rows)
    return int(cfg.descriptor_process_pool_min_rows)


def _calc_descriptor_row_values(
    idx: int,
    mol: Chem.Mol | None,
    disp_headers: list,
    int_fns: tuple,
    smarts_cache: dict,
    *,
    pka_states,
    pka_cache_used: bool,
) -> tuple[int, dict[str, str]]:
    row_ctx: dict = {"oid": int(idx)}
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
    return int(idx), row_data


def descriptor_callable_for_int_fn(i_f, smarts_cache, row_ctx=None):
    """Return ``callable(mol)`` for one internal descriptor id (shared by thread and process workers)."""
    ctx = row_ctx if row_ctx is not None else {}
    row_oid = ctx.get("oid")
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
            if row_oid is not None:
                return fingerprint_onbits_for_descriptor(i_f, int(row_oid))
            return fingerprint_onbits_for_internal_key(i_f)
    return lambda m: "N/A"


def _mp_calc_descriptor_row(args: tuple):
    """One row in a child process — avoids GIL contention with the Qt GUI thread."""
    idx, mol_bytes, disp_headers, int_fns, pka_states, pka_cache_used = args
    mol = None
    if mol_bytes:
        try:
            mol = Chem.Mol(mol_bytes)
        except Exception:
            mol = None
    return _calc_descriptor_row_values(
        idx,
        mol,
        list(disp_headers),
        tuple(int_fns),
        {},
        pka_states=pka_states,
        pka_cache_used=bool(pka_cache_used),
    )


def _mp_calc_descriptor_batch(args: tuple) -> list[tuple[int, dict[str, str]]]:
    """Several rows per child process — less IPC overhead on large fingerprint jobs."""
    items, disp_headers, int_fns, pka_cache_used = args
    smarts_cache: dict = {}
    out: list[tuple[int, dict[str, str]]] = []
    headers = list(disp_headers)
    fns = tuple(int_fns)
    for idx, mol_bytes, pka_states in items:
        mol = None
        if mol_bytes:
            try:
                mol = Chem.Mol(mol_bytes)
            except Exception:
                mol = None
        out.append(
            _calc_descriptor_row_values(
                idx,
                mol,
                headers,
                fns,
                smarts_cache,
                pka_states=pka_states,
                pka_cache_used=bool(pka_cache_used),
            )
        )
    return out


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
    batch_size: int,
    cancel_event: threading.Event | None,
    emit_progress,
) -> tuple[list, bool]:
    """
    Run descriptor rows in child processes (keeps the Qt GUI thread off the GIL).

    Returns ``(results, cancelled)``.
    """
    row_items: list[tuple[int, bytes, object]] = []
    for i, mol in prepared:
        blob = mol.ToBinary() if mol is not None else b""
        pka = pka_by_idx.get(i) if pka_cache_used else None
        row_items.append((i, blob, pka))
    batch_size = max(1, int(batch_size))
    batch_args: list[tuple] = []
    for start in range(0, len(row_items), batch_size):
        chunk = row_items[start : start + batch_size]
        batch_args.append((chunk, tuple(disp_headers), tuple(int_fns), bool(pka_cache_used)))
    proc_workers = min(max_workers, max(2, (os.cpu_count() or 4) - 1), 8)
    emit_progress(0, force=True)
    mp_results_dict: dict = {}
    done_count = 0
    cancelled = False
    last_pulse = 0.0
    ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
    try:
        pending = {ex.submit(_mp_calc_descriptor_batch, args) for args in batch_args}
        while pending:
            if should_terminate_process_pool(cancel_event):
                cancelled = True
                for f in list(pending):
                    if f.done() and not f.cancelled():
                        try:
                            for idx, row_d in f.result():
                                mp_results_dict[int(idx)] = row_d
                                done_count += 1
                        except Exception:
                            logger.exception("Process-pool descriptor batch failed")
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
                    for idx, row_d in f.result():
                        mp_results_dict[int(idx)] = row_d
                        done_count += 1
                    emit_progress(done_count)
                except Exception:
                    logger.exception("Process-pool descriptor batch failed")
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
    return _calc_descriptor_row_values(
        idx,
        mol,
        list(disp_headers),
        tuple(int_fns),
        smarts_cache,
        pka_states=pka_states,
        pka_cache_used=bool(pka_cache_used),
    )


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


@dataclass(frozen=True)
class SuperposeStructuresParams:
    """Options for aligning distinct table structures onto a reference molecule."""

    heavy_atoms_only: bool = True
    reflect: bool = False
    max_align_iters: int = 50
    align_pattern: str = ""
    align_pattern_is_smarts: bool = False
    use_mcs: bool = True


def _single_conformer_mol(mol: Chem.Mol, conf_id: int | None = None) -> Chem.Mol | None:
    """Return a copy of *mol* that keeps only one conformer."""
    if mol is None:
        return None
    try:
        m = Chem.Mol(mol)
    except Exception:
        return None
    try:
        nconf = int(m.GetNumConformers())
    except Exception:
        nconf = 0
    if nconf < 1:
        return None
    try:
        cids = sorted(int(c.GetId()) for c in m.GetConformers())
    except Exception:
        cids = list(range(nconf))
    if not cids:
        return None
    keep = int(cids[0] if conf_id is None else conf_id)
    if keep not in cids:
        keep = int(cids[0])
    try:
        conf = Chem.Conformer(m.GetConformer(keep))
        m.RemoveAllConformers()
        m.AddConformer(conf, assignId=True)
    except Exception:
        return None
    return m


def _parse_align_query(pattern: str, *, is_smarts: bool) -> Chem.Mol | None:
    pat = (pattern or "").strip()
    if not pat:
        return None
    try:
        return Chem.MolFromSmarts(pat) if is_smarts else Chem.MolFromSmiles(pat)
    except Exception:
        return None


def _atom_map_from_query(
    probe: Chem.Mol,
    ref: Chem.Mol,
    query: Chem.Mol,
    *,
    heavy_atoms_only: bool,
) -> list[tuple[int, int]] | None:
    try:
        prb_match = probe.GetSubstructMatch(query)
        ref_match = ref.GetSubstructMatch(query)
    except Exception:
        return None
    if not prb_match or not ref_match or len(prb_match) != len(ref_match):
        return None
    pairs = [(int(p), int(r)) for p, r in zip(prb_match, ref_match)]
    if heavy_atoms_only:
        pairs = [
            (p, r)
            for p, r in pairs
            if probe.GetAtomWithIdx(p).GetAtomicNum() != 1 and ref.GetAtomWithIdx(r).GetAtomicNum() != 1
        ]
    return pairs if len(pairs) >= 2 else None


def _atom_map_from_mcs(
    probe: Chem.Mol,
    ref: Chem.Mol,
    *,
    heavy_atoms_only: bool,
    timeout_s: int = 5,
) -> list[tuple[int, int]] | None:
    try:
        from rdkit.Chem import rdFMCS
    except Exception:
        return None
    try:
        res = rdFMCS.FindMCS(
            [ref, probe],
            timeout=int(timeout_s),
            matchValences=True,
            ringMatchesRingOnly=True,
            completeRingsOnly=False,
        )
    except Exception:
        return None
    if res is None or getattr(res, "canceled", False):
        return None
    smarts = getattr(res, "smartsString", "") or ""
    if not smarts or int(getattr(res, "numAtoms", 0) or 0) < 2:
        return None
    q = Chem.MolFromSmarts(smarts)
    if q is None:
        return None
    return _atom_map_from_query(probe, ref, q, heavy_atoms_only=heavy_atoms_only)


def _align_probe_to_ref_o3a(
    probe: Chem.Mol,
    ref: Chem.Mol,
    *,
    reflect: bool,
) -> tuple[float | None, str | None]:
    """Best-effort overlay when no atom map is available (Crippen O3A, then MMFF O3A)."""
    try:
        py_o3a = rdMolAlign.GetCrippenO3A(probe, ref)
        if py_o3a is not None:
            return float(py_o3a.Align()), "crippen_o3a"
    except Exception:
        logger.debug("Crippen O3A failed", exc_info=True)
    try:
        o3a = rdMolAlign.GetO3A(probe, ref)
        if o3a is not None:
            return float(o3a.Align()), "mmff_o3a"
    except Exception:
        logger.debug("MMFF O3A failed", exc_info=True)
    # Last resort: AlignMol without atomMap only works for identical atom counts / order.
    try:
        if probe.GetNumAtoms() == ref.GetNumAtoms() and probe.GetNumAtoms() >= 2:
            am = [(i, i) for i in range(probe.GetNumAtoms())]
            rms = float(
                rdMolAlign.AlignMol(
                    probe,
                    ref,
                    atomMap=am,
                    reflect=bool(reflect),
                    maxIters=50,
                )
            )
            return rms, "index_map"
    except Exception:
        logger.debug("Index-map AlignMol failed", exc_info=True)
    return None, None


def align_structure_onto_reference(
    probe: Chem.Mol,
    ref: Chem.Mol,
    params: SuperposeStructuresParams,
    cancel_event: threading.Event | None = None,
) -> tuple[Chem.Mol | None, dict]:
    """
    Rigidly align a copy of *probe* onto *ref*.

    Alignment preference: optional substructure pattern → MCS (when enabled) → O3A best overlay.
    """
    meta: dict = {"ok": False, "op": "superpose_structures"}
    if cancel_event is not None and cancel_event.is_set():
        meta["err"] = "cancelled"
        return None, meta
    prb = _single_conformer_mol(probe)
    reference = _single_conformer_mol(ref)
    if prb is None or reference is None:
        meta["err"] = "need_3d_conformers"
        return None, meta
    max_it = max(10, int(params.max_align_iters))
    method = ""
    atom_map: list[tuple[int, int]] | None = None
    pat = (params.align_pattern or "").strip()
    if pat:
        q = _parse_align_query(pat, is_smarts=bool(params.align_pattern_is_smarts))
        if q is None:
            meta["err"] = "invalid_align_pattern"
            return None, meta
        atom_map = _atom_map_from_query(
            prb, reference, q, heavy_atoms_only=bool(params.heavy_atoms_only)
        )
        if atom_map:
            method = "pattern"
        else:
            meta["pattern_miss"] = True
    if atom_map is None and params.use_mcs:
        atom_map = _atom_map_from_mcs(
            prb, reference, heavy_atoms_only=bool(params.heavy_atoms_only)
        )
        if atom_map:
            method = "mcs"
    rms: float | None = None
    if atom_map is not None:
        try:
            rms = float(
                rdMolAlign.AlignMol(
                    prb,
                    reference,
                    atomMap=atom_map,
                    reflect=bool(params.reflect),
                    maxIters=max_it,
                )
            )
        except Exception as e:
            logger.exception("structure AlignMol failed")
            meta["err"] = str(e)[:200]
            return None, meta
    else:
        rms, o3a_method = _align_probe_to_ref_o3a(prb, reference, reflect=bool(params.reflect))
        if rms is None or o3a_method is None:
            meta["err"] = "no_common_substructure_and_o3a_failed"
            return None, meta
        method = o3a_method
        atom_map = []
    meta["ok"] = True
    meta["method"] = method
    meta["rms"] = round(float(rms), 6)
    meta["n_align_atoms"] = len(atom_map) if atom_map else 0
    meta["heavy"] = bool(params.heavy_atoms_only)
    meta["reflect"] = bool(params.reflect)
    meta["max_align_iters"] = max_it
    if pat:
        meta["align_smarts"] = bool(params.align_pattern_is_smarts)
        meta["align_pattern"] = pat[:120]
    return prb, meta


def run_superpose_structures(
    ref_mol: Chem.Mol,
    probes: list[tuple[int, Chem.Mol]],
    params: SuperposeStructuresParams,
    *,
    ref_oid: int | None = None,
    cancel_event: threading.Event | None = None,
) -> list[tuple[int, Chem.Mol | None, dict]]:
    """
    Align each probe onto *ref_mol*.

    Returns one ``(oid, aligned_mol_or_None, meta)`` per probe. The reference row
    (*ref_oid*, when set) is returned as a single-conformer copy without realigning.
    """
    out: list[tuple[int, Chem.Mol | None, dict]] = []
    ref_single = _single_conformer_mol(ref_mol)
    ref_id = None if ref_oid is None else int(ref_oid)
    for oid, probe in probes:
        if cancel_event is not None and cancel_event.is_set():
            out.append((int(oid), None, {"ok": False, "err": "cancelled", "op": "superpose_structures"}))
            continue
        if ref_id is not None and int(oid) == ref_id:
            m = _single_conformer_mol(ref_single or ref_mol)
            out.append(
                (
                    int(oid),
                    m,
                    {
                        "ok": True,
                        "op": "superpose_structures",
                        "method": "reference",
                        "rms": 0.0,
                        "n_align_atoms": 0,
                    },
                )
            )
            continue
        aligned, meta = align_structure_onto_reference(
            probe,
            ref_mol if ref_single is None else ref_single,
            params,
            cancel_event=cancel_event,
        )
        out.append((int(oid), aligned, meta))
    return out


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


@dataclass(frozen=True)
class RmsdParams:
    """Options for :func:`run_conformer_rmsd` / :class:`RmsdWorker`."""

    reference_conformer_index: int = 0
    heavy_atoms_only: bool = True
    reflect: bool = False
    max_align_iters: int = 50
    align_pattern: str = ""
    align_pattern_is_smarts: bool = False
    source_column: str = "confs"


RMSD_HEADERS = ("RMSD_values", "RMSD_max", "RMSD_mean")


def run_conformer_rmsd(
    mol: Chem.Mol,
    params: RmsdParams,
    cancel_event: threading.Event | None = None,
) -> tuple[dict[str, str] | None, dict]:
    """
    Rigid-align each conformer to a reference and report RMSD (Å).

    Works on a copy of *mol* so input coordinates are unchanged. Reference RMSD is 0.
    """
    meta: dict = {"ok": False, "op": "rmsd"}
    if cancel_event is not None and cancel_event.is_set():
        meta["err"] = "cancelled"
        return None, meta
    try:
        m = Chem.Mol(mol)
    except Exception:
        meta["err"] = "bad_mol"
        return None, meta
    try:
        nconf = int(m.GetNumConformers())
    except Exception:
        nconf = 0
    if nconf < 1:
        meta["err"] = "no_conformers"
        return None, meta
    try:
        cids = sorted(int(c.GetId()) for c in m.GetConformers())
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
    sp = SuperposeParams(
        reference_conformer_index=ref_idx,
        heavy_atoms_only=bool(params.heavy_atoms_only),
        reflect=bool(params.reflect),
        max_align_iters=int(params.max_align_iters),
        align_pattern=(params.align_pattern or "").strip(),
        align_pattern_is_smarts=bool(params.align_pattern_is_smarts),
    )
    atom_map, map_err = _superpose_atom_map(m, sp)
    if map_err or not atom_map:
        meta["err"] = map_err or "no_atoms_for_alignment"
        return None, meta
    max_it = max(10, int(params.max_align_iters))
    rms_vals: list[float] = []
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
        logger.exception("run_conformer_rmsd failed")
        meta["err"] = str(e)[:200]
        return None, meta
    mean_rms = sum(rms_vals) / max(len(rms_vals), 1)
    max_rms = max(rms_vals) if rms_vals else 0.0
    meta["ok"] = True
    meta["ref_idx"] = ref_idx
    meta["ref_cid"] = ref_cid
    meta["ref_clamped"] = ref_clamped
    meta["n_conf"] = len(cids)
    meta["rms_mean"] = round(mean_rms, 6)
    meta["rms_max"] = round(max_rms, 6)
    meta["heavy"] = bool(params.heavy_atoms_only)
    meta["n_align_atoms"] = len(atom_map)
    row = {
        "RMSD_values": ";".join(f"{v:.4f}" for v in rms_vals),
        "RMSD_max": f"{max_rms:.4f}",
        "RMSD_mean": f"{mean_rms:.4f}",
    }
    return row, meta


def _rmsd_row_task(task: tuple) -> tuple[int, dict[str, str]]:
    oid, cell, params = task[0], task[1], task[2]
    cancel_event = task[3] if len(task) > 3 else None
    na = {h: "N/A" for h in RMSD_HEADERS}
    try:
        if cancel_event is not None and cancel_event.is_set():
            return oid, na
        mol = mol_from_packed_confs_cell(cell or "", min_conformers=1)
        if mol is None:
            return oid, na
        row, meta = run_conformer_rmsd(mol, params, cancel_event=cancel_event)
        if row is None or not meta.get("ok"):
            return oid, na
        return oid, {h: str(row.get(h, "N/A")) for h in RMSD_HEADERS}
    except Exception:
        logger.exception("RmsdWorker failed for oid=%s", oid)
        return oid, na


class RmsdWorker(QRunnable):
    """Score packed conformer cells; emit RMSD columns via ``calculated``."""

    def __init__(
        self,
        data: list[tuple[int, str]],
        params: RmsdParams,
        signals: WorkerSignals,
        cancel_event: threading.Event | None = None,
        progress_state=None,
        output_headers: list[str] | None = None,
    ):
        super().__init__()
        self.data = data
        self.params = params
        self.signals = signals
        self.cancel_event = cancel_event
        self.progress_state = progress_state
        if output_headers and len(output_headers) == len(RMSD_HEADERS):
            self.output_headers = list(output_headers)
        else:
            self.output_headers = list(RMSD_HEADERS)

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
        headers = list(self.output_headers)
        rename = dict(zip(RMSD_HEADERS, headers))
        try:
            if use_parallel:
                _emit_tool_progress_throttled(
                    self.signals,
                    "Calculate RMSD…",
                    0,
                    tot,
                    prog_state,
                    progress_state=self.progress_state,
                )
                ex = ThreadPoolExecutor(max_workers=max_workers)
                shutdown_cancel = False
                try:
                    row_tasks = [(*t, cancel_ev) for t in tasks]
                    pending = {ex.submit(_rmsd_row_task, rt) for rt in row_tasks}
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
                                        logger.exception("RMSD row task failed")
                                else:
                                    f.cancel()
                            break
                        completed, pending = wait(
                            pending, timeout=0.08, return_when=FIRST_COMPLETED
                        )
                        for f in completed:
                            if f.cancelled():
                                continue
                            try:
                                results.append(f.result())
                                done_count += 1
                            except Exception:
                                logger.exception("RMSD row task failed")
                            _emit_tool_progress_throttled(
                                self.signals,
                                "Calculate RMSD…",
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
                    "Calculate RMSD…",
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
                    results.append(_rmsd_row_task((*t, cancel_ev)))
                    done_count = done
                    _emit_tool_progress_throttled(
                        self.signals,
                        "Calculate RMSD…",
                        done,
                        tot,
                        prog_state,
                        progress_state=self.progress_state,
                    )
        except Exception:
            logger.exception("RmsdWorker failed")
        finally:
            emit_partial_results_if_cancelled(
                self.signals, "Calculate RMSD", done_count, tot, cancelled
            )
            mapped: list[tuple[int, dict[str, str]]] = []
            for oid, row in results:
                mapped.append((int(oid), {rename.get(k, k): v for k, v in row.items()}))
            try:
                self.signals.calculated.emit(mapped, headers)
            except Exception:
                logger.warning("RMSD calculated emit failed", exc_info=True)


@dataclass(frozen=True)
class StrainEnergyParams:
    """Options for :func:`run_strain_energy` / :class:`StrainEnergyWorker`."""

    reference_conformer_index: int = 0
    force_field: str = "MMFF"
    source_column: str = "confs"


STRAIN_ENERGY_HEADERS = ("Strain_energies", "Strain_max", "E_ref")


def _single_point_conformer_energies(
    mol: Chem.Mol, force_field: str
) -> tuple[list[float], str] | None:
    """
    Single-point MMFF/UFF energies (kcal/mol) for each conformer — no minimization.

    Adds hydrogens with coordinates when needed for the force field.
    """
    try:
        m = Chem.AddHs(Chem.Mol(mol), addCoords=True)
    except Exception:
        return None
    try:
        cids = sorted(int(c.GetId()) for c in m.GetConformers())
    except Exception:
        cids = list(range(int(m.GetNumConformers())))
    if not cids:
        return None
    ff_choice = (force_field or "MMFF").strip().upper()
    energies: list[float] = []
    if ff_choice == "MMFF":
        mp = AllChem.MMFFGetMoleculeProperties(m)
        if mp is not None:
            for cid in cids:
                ff = AllChem.MMFFGetMoleculeForceField(m, mp, confId=int(cid))
                if ff is None:
                    return None
                energies.append(float(ff.CalcEnergy()))
            return energies, "MMFF"
    for cid in cids:
        ff = AllChem.UFFGetMoleculeForceField(m, confId=int(cid))
        if ff is None:
            return None
        energies.append(float(ff.CalcEnergy()))
    return energies, "UFF"


def run_strain_energy(
    mol: Chem.Mol,
    params: StrainEnergyParams,
    cancel_event: threading.Event | None = None,
) -> tuple[dict[str, str] | None, dict]:
    """
    Compute strain energy of each conformer relative to a reference conformer.

    Strain_i = E_i − E_ref (kcal/mol) from a single-point force-field evaluation
    (coordinates are not re-minimized).
    """
    meta: dict = {"ok": False, "op": "strain"}
    if cancel_event is not None and cancel_event.is_set():
        meta["err"] = "cancelled"
        return None, meta
    if mol is None or mol.GetNumAtoms() == 0:
        meta["err"] = "empty_molecule"
        return None, meta
    try:
        nconf = int(mol.GetNumConformers())
    except Exception:
        nconf = 0
    if nconf < 1:
        meta["err"] = "no_conformers"
        return None, meta

    opt = _single_point_conformer_energies(mol, params.force_field)
    if opt is None:
        meta["err"] = "energy_failed"
        return None, meta
    energies, ff = opt
    meta["ff"] = ff
    meta["n_conf"] = len(energies)
    ref_idx = int(params.reference_conformer_index)
    if ref_idx < 0:
        ref_idx = 0
    ref_clamped = False
    if ref_idx >= len(energies):
        ref_idx = len(energies) - 1
        ref_clamped = True
    e_ref = float(energies[ref_idx])
    strains = [float(e) - e_ref for e in energies]
    meta["ok"] = True
    meta["ref_idx"] = ref_idx
    meta["ref_clamped"] = ref_clamped
    meta["e_ref_kcal"] = round(e_ref, 4)
    meta["strain_max_kcal"] = round(max(strains), 4) if strains else 0.0
    meta["energies"] = [round(float(e), 4) for e in energies]
    meta["strains"] = [round(float(s), 4) for s in strains]
    row = {
        "Strain_energies": ";".join(f"{s:.4f}" for s in strains),
        "Strain_max": f"{max(strains):.4f}" if strains else "0.0000",
        "E_ref": f"{e_ref:.4f}",
    }
    return row, meta


def _strain_energy_row_task(task: tuple) -> tuple[int, dict[str, str]]:
    oid, cell, params = task[0], task[1], task[2]
    cancel_event = task[3] if len(task) > 3 else None
    na = {h: "N/A" for h in STRAIN_ENERGY_HEADERS}
    try:
        if cancel_event is not None and cancel_event.is_set():
            return oid, na
        mol = mol_from_packed_confs_cell(cell or "", min_conformers=1)
        if mol is None:
            return oid, na
        row, meta = run_strain_energy(mol, params, cancel_event=cancel_event)
        if row is None or not meta.get("ok"):
            return oid, na
        return oid, {h: str(row.get(h, "N/A")) for h in STRAIN_ENERGY_HEADERS}
    except Exception:
        logger.exception("StrainEnergyWorker failed for oid=%s", oid)
        return oid, na


class StrainEnergyWorker(QRunnable):
    """Score packed conformer cells; emit descriptor-style columns via ``calculated``."""

    def __init__(
        self,
        data: list[tuple[int, str]],
        params: StrainEnergyParams,
        signals: WorkerSignals,
        cancel_event: threading.Event | None = None,
        progress_state=None,
        output_headers: list[str] | None = None,
    ):
        super().__init__()
        self.data = data
        self.params = params
        self.signals = signals
        self.cancel_event = cancel_event
        self.progress_state = progress_state
        if output_headers and len(output_headers) == len(STRAIN_ENERGY_HEADERS):
            self.output_headers = list(output_headers)
        else:
            self.output_headers = list(STRAIN_ENERGY_HEADERS)

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
        headers = list(self.output_headers)
        rename = dict(zip(STRAIN_ENERGY_HEADERS, headers))
        try:
            if use_parallel:
                _emit_tool_progress_throttled(
                    self.signals,
                    "Calculate strain energy…",
                    0,
                    tot,
                    prog_state,
                    progress_state=self.progress_state,
                )
                ex = ThreadPoolExecutor(max_workers=max_workers)
                shutdown_cancel = False
                try:
                    row_tasks = [(*t, cancel_ev) for t in tasks]
                    pending = {ex.submit(_strain_energy_row_task, rt) for rt in row_tasks}
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
                                        logger.exception("Strain energy row task failed")
                                else:
                                    f.cancel()
                            break
                        completed, pending = wait(
                            pending, timeout=0.08, return_when=FIRST_COMPLETED
                        )
                        for f in completed:
                            if f.cancelled():
                                continue
                            try:
                                results.append(f.result())
                                done_count += 1
                            except Exception:
                                logger.exception("Strain energy row task failed")
                            _emit_tool_progress_throttled(
                                self.signals,
                                "Calculate strain energy…",
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
                    "Calculate strain energy…",
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
                    results.append(_strain_energy_row_task((*t, cancel_ev)))
                    done_count = done
                    _emit_tool_progress_throttled(
                        self.signals,
                        "Calculate strain energy…",
                        done,
                        tot,
                        prog_state,
                        progress_state=self.progress_state,
                    )
        except Exception:
            logger.exception("StrainEnergyWorker failed")
        finally:
            emit_partial_results_if_cancelled(
                self.signals, "Calculate strain energy", done_count, tot, cancelled
            )
            mapped: list[tuple[int, dict[str, str]]] = []
            for oid, row in results:
                mapped.append((int(oid), {rename.get(k, k): v for k, v in row.items()}))
            try:
                self.signals.calculated.emit(mapped, headers)
            except Exception:
                logger.warning("strain energy calculated emit failed", exc_info=True)


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

        pka_by_idx: dict[int, list | None] = {}
        pka_cache_used = False
        if int_fns_need_pkasolver(self.int_fns) and nrows > 0:
            pka_by_idx = build_microstates_cache_for_rows(
                prepared,
                cancel_event=self.cancel_event,
                progress_state=self.progress_state,
                signals=self.signals,
                progress_message="Calculate descriptors",
                progress_total=tot,
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

            # If the user cancelled while pkasolver was still computing microstates, we can still
            # return partial descriptor results for the structures whose microstates we already
            # have. This prevents "blank columns" after cancellation for LogD/LogS and similar
            # pkasolver-dependent descriptors.
            if cancel_ev is not None and cancel_ev.is_set():
                cancelled = True
                results = []
                done_count = 0
                _emit_progress(0, force=True)
                for i, mol in prepared:
                    if pka_by_idx.get(i) is None:
                        continue
                    try:
                        results.append(
                            _calc_descriptor_row_task(
                                (
                                    i,
                                    mol,
                                    self.disp_headers,
                                    tuple(self.int_fns),
                                    smarts_cache,
                                    pka_by_idx.get(i),
                                    True,
                                )
                            )
                        )
                        done_count += 1
                    except Exception:
                        logger.exception("Descriptor row task failed (partial-cancel path)")
                    _emit_progress(min(done_count, tot))
                _emit_progress(min(done_count, tot), force=True)
                emit_partial_results_if_cancelled(
                    self.signals, "Calculate descriptors", len(results), tot, cancelled
                )
                self.signals.calculated.emit(results, self.disp_headers)
                return
        # ThreadPoolExecutor row tasks so RDKit never runs on the Qt GUI thread and small jobs
        # still use a worker thread instead of the process-queue thread doing every row inline.
        # RDKit descriptor / fingerprint work holds the GIL; child processes keep Qt responsive.
        mp_min = _descriptor_process_pool_min_rows(cfg, self.int_fns)
        use_process_pool = nrows >= mp_min and nrows >= 2 and max_workers > 1
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
                    batch_size=int(cfg.descriptor_process_pool_batch_size),
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

