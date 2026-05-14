"""Background pKa prediction using pkasolver (mayrf/pkasolver)."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import threading
import types
import warnings

from PyQt5 import sip
from PyQt5.QtCore import QObject, QRunnable, pyqtSignal
from rdkit import Chem

logger = logging.getLogger(__name__)

_query_model_singleton = None

# Loggers that become noisy when pkasolver imports RDKit PandasTools (pandas 3.x API drift) or logs steps.
_PKA_SUPPRESSED_LOGGER_NAMES = (
    "rdkit.Chem.PandasPatcher",
    "rdkit.Chem.PandasTools",
    "pkasolver",
    "pkasolver.query",
)


@contextlib.contextmanager
def _quieter_pkasolver_dependency_loggers():
    """Temporarily raise log levels so RDKit/pkasolver chatter does not flood the ChemManager console."""
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
def isolated_sys_argv_for_embedded_cli():
    """Dimorphite-DL and similar tools parse ``sys.argv``; strip ChemManager flags (e.g. ``-o file``)."""
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


def _ensure_cairosvg_importable() -> None:
    """pkasolver.query imports cairosvg at module level; stub it if native Cairo is unavailable."""
    if "cairosvg" in sys.modules:
        return
    try:
        import cairosvg  # noqa: F401
    except Exception:

        def _svg2png(**_kwargs):
            return None

        stub = types.ModuleType("cairosvg")
        stub.svg2png = _svg2png
        sys.modules["cairosvg"] = stub


def _patch_pkasolver_dimorphite() -> None:
    """Use in-process Dimorphite-DL instead of pkasolver's subprocess + test.pkl path."""
    import pkasolver.query as pq
    from pkasolver import run_with_mol_list

    if getattr(pq, "_CHEMMANAGER_DIMORPHITE_PATCHED", False):
        return

    def _inline(mol, min_ph, max_ph, pka_precision=1.0):
        # Dimorphite-DL's main() calls argparse on sys.argv before merging kwargs, so ChemManager
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
    pq._CHEMMANAGER_DIMORPHITE_PATCHED = True


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
    ):
        super().__init__()
        self.rows = rows
        self.worker_signals = worker_signals
        self.pka_signals = pka_signals
        self.cancel_event = cancel_event
        self.most_basic_only = most_basic_only
        self.most_acidic_only = most_acidic_only

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

            tot = max(len(self.rows), 1)
            cancel_ev = self.cancel_event
            out: list[tuple[int | None, str]] = []

            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    if _query_model_singleton is None:
                        _query_model_singleton = QueryModel()
                    qm = _query_model_singleton
            except Exception as e:
                logger.exception("pKa predictor: model load failed")
                _safe_emit(self.pka_signals, "failed", f"Could not load pkasolver neural models: {e}")
                return

            try:
                self.worker_signals.tool_progress.emit("pKa prediction…", 0, tot)
            except Exception:
                pass

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                for done, (oid, mol) in enumerate(self.rows, start=1):
                    if cancel_ev is not None and cancel_ev.is_set():
                        break
                    try:
                        self.worker_signals.tool_progress.emit("pKa prediction…", done, tot)
                    except Exception:
                        pass
                    if mol is None:
                        out.append((oid, "N/A"))
                        continue
                    try:
                        with _discard_stdio(), isolated_sys_argv_for_embedded_cli():
                            states = calculate_microstate_pka_values(mol, query_model=qm)
                        out.append(
                            (
                                oid,
                                _format_microstate_pkas(
                                    states,
                                    most_basic_only=self.most_basic_only,
                                    most_acidic_only=self.most_acidic_only,
                                ),
                            )
                        )
                    except Exception as e:
                        logger.warning("pKa prediction failed for row %s: %s", oid, e)
                        out.append((oid, f"Error: {e}"))

            try:
                self.worker_signals.tool_progress.emit("pKa prediction…", tot, tot)
            except Exception:
                pass
            _safe_emit(self.pka_signals, "finished", out)
