"""Background pKa prediction using pkasolver (mayrf/pkasolver).

**pkasolver** — graph neural network microstate pKas: Mayr, F.; Wieder, M.; Wieder, O.; Langer, T.
*Improving Small Molecule pKa Prediction Using Transfer Learning With Graph Neural Networks.*
Front. Chem. 2022, 10, 866585. https://doi.org/10.3389/fchem.2022.866585
Code: https://github.com/mayrf/pkasolver

**Dimorphite-DL** (protonation-state enumeration inside pkasolver): Ropp, P. J.; et al.
*J. Cheminform.* 2019, 11, 14. https://doi.org/10.1186/s13321-019-0336-9

Shorter copy-paste block: ``molmanager.science_citations.PKASOLVER`` and ``.DIMORPHITE_DL``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import threading
import time
import types
import warnings
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from .process_pool_utils import (
    application_is_shutting_down,
    register_process_pool,
    should_terminate_process_pool,
    shutdown_process_pool_executor,
)

from PyQt5 import sip
from PyQt5.QtCore import QObject, QRunnable, pyqtSignal
from rdkit import Chem

from .structure_grouping import group_rows_by_structure

logger = logging.getLogger(__name__)

_query_model_singleton = None
# pkasolver / PyTorch QueryModel is not safe for concurrent use from multiple threads.
_query_model_lock = threading.Lock()

# Loggers that become noisy when pkasolver imports RDKit PandasTools (pandas 3.x API drift) or logs steps.
_PKA_SUPPRESSED_LOGGER_NAMES = (
    "rdkit.Chem.PandasPatcher",
    "rdkit.Chem.PandasTools",
    "pkasolver",
    "pkasolver.query",
)


@contextlib.contextmanager
def _quieter_pkasolver_dependency_loggers():
    """Temporarily raise log levels so RDKit/pkasolver chatter does not flood the molmanager console."""
    saved: list[tuple[logging.Logger, int]] = []
    for name in _PKA_SUPPRESSED_LOGGER_NAMES:
        lg = logging.getLogger(name)
        saved.append((lg, lg.level))
        lg.setLevel(logging.ERROR)
    try:
        yield
    finally:
        for lg, prev in saved:
            lg.setLevel(prev)


@contextlib.contextmanager
def _discard_stdio():
    """Hide pkasolver ``print`` output and Dimorphite help banners (they bypass logging)."""
    with open(os.devnull, "w", encoding="utf-8") as dn:
        with contextlib.redirect_stdout(dn), contextlib.redirect_stderr(dn):
            yield


@contextlib.contextmanager
def _discard_stdout_only():
    """Hide chatty ``print`` on stdout while keeping stderr for tracebacks in the console."""
    with open(os.devnull, "w", encoding="utf-8") as dn:
        with contextlib.redirect_stdout(dn):
            yield


@contextlib.contextmanager
def isolated_sys_argv_for_embedded_cli():
    """Dimorphite-DL and similar tools parse ``sys.argv``; strip molmanager flags (e.g. ``-o file``)."""
    old = sys.argv[:]
    prog = old[0] if old else "python"
    sys.argv = [prog]
    try:
        yield
    finally:
        sys.argv = old


def _safe_emit(obj, emitter_name: str, *args) -> None:
    """Emit on a QObject-owned signal if the C++ object still exists (avoids shutdown / close races)."""
    if obj is None:
        return
    try:
        if sip.isdeleted(obj):
            return
    except Exception:
        return
    try:
        getattr(obj, emitter_name).emit(*args)
    except RuntimeError:
        pass


def _acquire_lock_cooperative(lock: threading.Lock, cancel_event: threading.Event | None, timeout_s: float = 0.05) -> bool:
    """Acquire lock in short slices so cancellation can abort long waits."""
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return False
        if lock.acquire(timeout=timeout_s):
            return True


def _ensure_cairosvg_importable() -> None:
    """pkasolver.query imports cairosvg at module level; stub it if native Cairo is unavailable."""
    existing = sys.modules.get("cairosvg")
    if existing is not None and getattr(existing, "_MOLMANAGER_CAIROSVG_STUB", False):
        return
    if existing is not None:
        for name in list(sys.modules):
            if name == "cairosvg" or name.startswith("cairosvg."):
                del sys.modules[name]
    try:
        import cairosvg  # noqa: F401
    except Exception:

        def _svg2png(**_kwargs):
            return None

        stub = types.ModuleType("cairosvg")
        stub.svg2png = _svg2png
        stub._MOLMANAGER_CAIROSVG_STUB = True
        sys.modules["cairosvg"] = stub


def _patch_pkasolver_dimorphite() -> None:
    """Use in-process Dimorphite-DL instead of pkasolver's subprocess + test.pkl path."""
    import pkasolver.query as pq
    from pkasolver import run_with_mol_list

    if getattr(pq, "_MOLMANAGER_DIMORPHITE_PATCHED", False):
        return

    def _inline(mol, min_ph, max_ph, pka_precision=1.0):
        # Dimorphite-DL's main() calls argparse on sys.argv before merging kwargs, so molmanager
        # flags (e.g. ``-o file.sdf``) would otherwise be parsed as Dimorphite args and fail.
        old_argv = sys.argv[:]
        prog = old_argv[0] if old_argv else "python"
        sys.argv = [prog]
        try:
            return run_with_mol_list(
                [mol],
                min_ph=float(min_ph),
                max_ph=float(max_ph),
                pka_precision=float(pka_precision),
                silent=True,
            )
        finally:
            sys.argv = old_argv

    pq._call_dimorphite_dl = _inline
    pq._MOLMANAGER_DIMORPHITE_PATCHED = True


def _mol_props_dict_safe(mol: Chem.Mol) -> None:
    """Raise ``UnicodeDecodeError`` if Dimorphite-style ``GetPropsAsDict`` would fail."""
    fn = getattr(mol, "GetPropsAsDict", None)
    if callable(fn):
        fn()


def _strip_mol_props_with_bad_encoding(mol: Chem.Mol) -> None:
    """Remove SDF tags whose values are not valid UTF-8 (common in legacy drug SD files)."""
    for key in list(mol.GetPropNames(includePrivate=True)):
        try:
            mol.GetProp(key)
        except UnicodeDecodeError:
            mol.ClearProp(key)


def prepare_mol_for_pkasolver(mol: Chem.Mol | None) -> Chem.Mol | None:
    """
    Return a molecule safe for pkasolver / Dimorphite (``GetPropsAsDict`` expects UTF-8 strings).

    Many SD files (e.g. vendor deposits) attach Latin-1 or binary metadata; pkasolver then raises
    ``UnicodeDecodeError``. We first drop unreadable properties on a copy, then fall back to an
    isomeric SMILES round-trip (structure only, no tags).
    """
    if mol is None or mol.GetNumAtoms() == 0:
        return None
    copy = Chem.Mol(mol)
    _strip_mol_props_with_bad_encoding(copy)
    try:
        _mol_props_dict_safe(copy)
        return copy
    except UnicodeDecodeError:
        pass
    try:
        smi = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    except Exception:
        return None
    if not smi:
        return None
    return Chem.MolFromSmiles(smi)


def _mp_compute_pka_text(
    task: tuple[str, bytes, bool, bool],
) -> tuple[str, str]:
    """
    Child-process entry: load pkasolver, predict one structure, return formatted pKa text.

    Each process keeps its own ``QueryModel`` so jobs can run in parallel (RAM trade-off).
    """
    key, mol_blob, most_basic_only, most_acidic_only = task
    if not mol_blob:
        return key, "N/A"
    with _quieter_pkasolver_dependency_loggers():
        try:
            _ensure_cairosvg_importable()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                _patch_pkasolver_dimorphite()
                from pkasolver.query import QueryModel, calculate_microstate_pka_values
        except Exception:
            logger.exception("pKa subprocess: pkasolver import failed")
            return key, "Error (see log)"
    try:
        mol = Chem.Mol(mol_blob)
    except Exception:
        return key, "N/A"
    if mol is None or mol.GetNumAtoms() == 0:
        return key, "N/A"
    safe = prepare_mol_for_pkasolver(mol)
    if safe is None:
        return key, "N/A"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            qm = QueryModel()
        with _discard_stdout_only(), isolated_sys_argv_for_embedded_cli():
            states = calculate_microstate_pka_values(safe, query_model=qm)
        txt = _format_microstate_pkas(
            states,
            most_basic_only=most_basic_only,
            most_acidic_only=most_acidic_only,
        )
        return key, txt
    except UnicodeDecodeError:
        return key, "N/A (SDF metadata)"
    except Exception:
        logger.exception("pKa subprocess: prediction failed for key=%s", key[:48])
        return key, "Error (see log)"


def _format_microstate_pkas(
    states, *, most_basic_only: bool = False, most_acidic_only: bool = False
) -> str:
    if not states:
        return "N/A"
    vals = [float(s.pka) for s in states]
    if most_basic_only and most_acidic_only:
        most_acidic_only = False
    if most_basic_only:
        return f"{max(vals):.2f}"
    if most_acidic_only:
        return f"{min(vals):.2f}"
    vals.sort()
    parts = [f"{v:.2f}" for v in vals[:12]]
    tail = " …" if len(vals) > 12 else ""
    return "; ".join(parts) + tail


class PKaPredictorSignals(QObject):
    """Emits from :class:`PKaPredictorWorker` back to the dialog (owned on the GUI thread)."""

    finished = pyqtSignal(list)  # list[tuple[int | None, str]]  oid None = SMILES-only preview
    failed = pyqtSignal(str)


class PKaPredictorWorker(QRunnable):
    """Predict microstate pKa values per row; writes are applied on the GUI thread via ``finished``."""

    def __init__(
        self,
        rows: list[tuple[int | None, Chem.Mol | None]],
        worker_signals,
        pka_signals: PKaPredictorSignals,
        cancel_event: threading.Event | None = None,
        *,
        most_basic_only: bool = False,
        most_acidic_only: bool = False,
        progress_state=None,
    ):
        super().__init__()
        self.rows = rows
        self.worker_signals = worker_signals
        self.pka_signals = pka_signals
        self.cancel_event = cancel_event
        self.most_basic_only = most_basic_only
        self.most_acidic_only = most_acidic_only
        self.progress_state = progress_state

    def run(self) -> None:
        global _query_model_singleton
        with _quieter_pkasolver_dependency_loggers():
            try:
                _ensure_cairosvg_importable()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    _patch_pkasolver_dimorphite()
                    from pkasolver.query import QueryModel, calculate_microstate_pka_values
            except Exception as e:
                logger.exception("pKa predictor: failed to import pkasolver stack")
                _safe_emit(
                    self.pka_signals,
                    "failed",
                    "Could not load pkasolver (missing PyTorch / torch-geometric / pkasolver?). "
                    f"Details: {e}",
                )
                return

            cancel_ev = self.cancel_event
            row_text: dict[int | None, str] = {}
            for oid, mol in self.rows:
                if mol is None:
                    row_text[oid] = "N/A"

            order, rep, oids_map = group_rows_by_structure(self.rows)
            n_work = sum(len(oids_map[k]) for k in order) + sum(1 for oid, mol in self.rows if mol is None)
            tot = max(n_work, 1)
            n_unique = len(order)

            from ..config import load_config
            from .pkasolver_parallel import plan_pkasolver_process_workers

            use_mp, proc_workers = plan_pkasolver_process_workers(
                n_unique, load_config().pka_process_workers
            )

            done_cum = sum(1 for oid, mol in self.rows if mol is None)
            cancelled = False
            prog_last = 0.0

            from ..tool_progress import report_tool_progress

            throttle = [0, 0.0]

            def _emit(done: int, *, force: bool = False) -> None:
                nonlocal prog_last
                now = time.monotonic()
                if force or done >= tot or (now - prog_last) >= 0.12:
                    prog_last = now
                    report_tool_progress(
                        message="pKa prediction",
                        done=min(done, tot),
                        total=tot,
                        progress_state=self.progress_state,
                        signals=self.worker_signals,
                        throttle=throttle,
                        force_signal=force,
                    )

            _emit(done_cum, force=True)

            if not order:
                out = [(oid, row_text.get(oid, "N/A")) for oid, _ in self.rows]
                _emit(tot, force=True)
                _safe_emit(self.pka_signals, "finished", out)
                return

            if use_mp:
                tasks = [
                    (k, rep[k].ToBinary(), self.most_basic_only, self.most_acidic_only) for k in order
                ]
                results_by_key: dict[str, str] = {}
                user_cancelled = False
                ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
                try:
                    pending = {ex.submit(_mp_compute_pka_text, t) for t in tasks}
                    while pending:
                        if should_terminate_process_pool(cancel_ev) or application_is_shutting_down():
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
                                key, txt = f.result()
                                results_by_key[key] = txt
                                done_cum += len(oids_map.get(key, ()))
                            except Exception:
                                logger.exception("pKa process-pool task failed")
                            _emit(done_cum)
                finally:
                    shutdown_process_pool_executor(
                        ex, kill_workers=should_terminate_process_pool(cancel_ev)
                    )
                for key in order:
                    txt = results_by_key.get(key, "Error (see log)")
                    for oid in oids_map.get(key, ()):
                        row_text[oid] = txt
            else:
                try:
                    if not _acquire_lock_cooperative(_query_model_lock, cancel_ev):
                        cancelled = True
                        out = [(oid, row_text.get(oid, "N/A")) for oid, _ in self.rows]
                        _safe_emit(self.pka_signals, "finished", out)
                        return
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", FutureWarning)
                            if _query_model_singleton is None:
                                _query_model_singleton = QueryModel()
                            qm = _query_model_singleton
                    finally:
                        _query_model_lock.release()
                except Exception as e:
                    logger.exception("pKa predictor: model load failed")
                    _safe_emit(self.pka_signals, "failed", f"Could not load pkasolver neural models: {e}")
                    return

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    for key in order:
                        if should_terminate_process_pool(cancel_ev):
                            cancelled = True
                            break
                        mol = rep[key]
                        safe_mol = prepare_mol_for_pkasolver(mol)
                        if safe_mol is None:
                            txt = "N/A"
                        else:
                            try:
                                with _discard_stdout_only(), isolated_sys_argv_for_embedded_cli():
                                    if not _acquire_lock_cooperative(_query_model_lock, cancel_ev):
                                        cancelled = True
                                        break
                                    try:
                                        states = calculate_microstate_pka_values(safe_mol, query_model=qm)
                                    finally:
                                        _query_model_lock.release()
                                txt = _format_microstate_pkas(
                                    states,
                                    most_basic_only=self.most_basic_only,
                                    most_acidic_only=self.most_acidic_only,
                                )
                            except UnicodeDecodeError as e:
                                logger.warning(
                                    "pKa prediction skipped %s row(s) (non-UTF8 structure metadata): %s",
                                    len(oids_map[key]),
                                    e,
                                )
                                txt = "N/A (SDF metadata)"
                            except Exception:
                                logger.exception(
                                    "pKa prediction failed for %s row(s) (key prefix %.40s…)",
                                    len(oids_map[key]),
                                    key,
                                )
                                txt = "Error (see log)"
                        for oid in oids_map[key]:
                            row_text[oid] = txt
                        done_cum += len(oids_map[key])
                        _emit(done_cum)

            out = [(oid, row_text.get(oid, "N/A")) for oid, _ in self.rows]
            _emit(tot, force=True)
            if cancelled and done_cum > 0:
                try:
                    self.worker_signals.partial_results.emit("pKa prediction", done_cum, tot)
                except Exception:
                    pass
            if use_mp:
                logger.debug(
                    "pKa: %s table row(s), %s unique structure(s), process pool=%s",
                    n_work,
                    n_unique,
                    proc_workers,
                )
            else:
                logger.debug(
                    "pKa: %s table row(s), %s unique structure(s), sequential (set "
                    "MOLMANAGER_PKA_PROCESS_WORKERS>=1 to allow worker processes; <=0 disables)",
                    n_work,
                    n_unique,
                )
            _safe_emit(self.pka_signals, "finished", out)
