"""File load, 2D render, and fragment wash workers."""

import csv
import logging
import os
import threading
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from .process_pool_utils import (
    register_process_pool,
    should_terminate_process_pool,
    shutdown_process_pool_executor,
)

from PyQt5.QtCore import QRunnable
from rdkit import Chem

from ..display_constants import STRUCTURE_DEPICT_HEIGHT, STRUCTURE_DEPICT_WIDTH
from ..config import load_config
from ..import_structure import needs_structure_source_picker
from ..ingest_text import csv_row_to_cells, smi_line_to_cells
from ..fragment_disconnect import largest_fragment_and_rest
from ..structure_draw import render_molecule_png
from ..structure_neutralize import neutralize_mol
from ..structure_hydrogens import add_explicit_hydrogens, remove_explicit_hydrogens
from ..utils import parse_molecule_from_cell_text, safe_mol_prop_string
from .signals import WorkerSignals, emit_partial_results_if_cancelled

logger = logging.getLogger(__name__)


def _mp_render_structure_row(args: tuple):
    """Cairo 2D structure render in a child process (picklable args)."""
    oid, mol_bytes, w, h, skip_mol_props, batch_session = args
    sid = int(batch_session or 0)
    if not mol_bytes:
        return int(oid), {}, b"", False, int(w), int(h), sid
    try:
        mol = Chem.Mol(mol_bytes)
        if skip_mol_props:
            p = {}
        else:
            p = {n: safe_mol_prop_string(mol, n) for n in mol.GetPropNames()}
        png = render_molecule_png(mol, int(w), int(h))
        return int(oid), p, png, True, int(w), int(h), sid
    except Exception:
        return int(oid), {}, b"", False, int(w), int(h), sid


class Render2DBatchHeldJob(QRunnable):
    """
    Process-queue adapter for Tools → Render 2D.

    Prepares the batch on the GUI thread, runs subprocess rendering, then blocks until the
    UI has flushed results (``_render2d_batch_done_event``).
    """

    def __init__(self, app, payload: tuple, cancel_event: threading.Event | None) -> None:
        super().__init__()
        self._app = app
        self._payload = payload
        self._cancel_event = cancel_event

    def run(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            return
        done_ev = getattr(self._app, "_render2d_batch_done_event", None)
        if done_ev is not None:
            done_ev.clear()
        try:
            from PyQt5.QtCore import QMetaObject, Qt

            self._app._render2d_queue_payload = self._payload
            self._app._render2d_queue_cancel_event = self._cancel_event
            QMetaObject.invokeMethod(
                self._app,
                "_begin_render2d_batch_from_queue",
                Qt.BlockingQueuedConnection,
            )
        except Exception:
            logger.exception("Render2D batch UI prepare failed")
            if done_ev is not None:
                done_ev.set()
            return
        if done_ev is not None:
            if not getattr(self._app, "render2d_batch_active", lambda: False)():
                done_ev.set()
            else:
                done_ev.wait()


class Render2DBatchProcessWorker(QRunnable):
    """Tools → Render 2D: draw structures in subprocesses so the GUI process stays responsive."""

    def __init__(
        self,
        items: list,
        signals: WorkerSignals,
        cancel_event: threading.Event | None,
        batch_session: int,
    ):
        super().__init__()
        self.items = list(items)
        self.signals = signals
        self.cancel_event = cancel_event
        self.batch_session = int(batch_session or 0)

    def run(self) -> None:
        ev = self.cancel_event
        tasks: list[tuple] = []
        for oid, mol, w, h in self.items:
            if ev is not None and ev.is_set():
                break
            try:
                blob = mol.ToBinary() if mol is not None else b""
            except Exception:
                blob = b""
            tasks.append((int(oid), blob, int(w), int(h), True, self.batch_session))

        tot = len(tasks)
        if tot == 0:
            return

        ncpu = os.cpu_count() or 4
        proc_workers = min(8, max(2, ncpu - 1), 6)
        max_inflight = min(48, max(proc_workers * 4, proc_workers))

        it = iter(tasks)
        pending = set()
        user_cancelled = False
        ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))

        def _fill() -> None:
            while len(pending) < max_inflight:
                t = next(it, None)
                if t is None:
                    break
                pending.add(ex.submit(_mp_render_structure_row, t))

        try:
            _fill()
            while pending:
                if should_terminate_process_pool(ev):
                    user_cancelled = True
                    for f in pending:
                        f.cancel()
                    break
                completed, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for f in completed:
                    if f.cancelled():
                        continue
                    try:
                        idx, p, img, ok, w, h, sid = f.result()
                        self.signals.rendered.emit(idx, p, img, ok, w, h, sid)
                    except Exception:
                        logger.exception("Render2D batch subprocess row failed")
                _fill()
        finally:
            shutdown_process_pool_executor(ex, kill_workers=should_terminate_process_pool(ev))


class Render2DBatchChunkRunner(QRunnable):
    """Run one Render 2D chunk and notify the app on the GUI thread when finished."""

    def __init__(self, worker: Render2DBatchProcessWorker, on_done) -> None:
        super().__init__()
        self._worker = worker
        self._on_done = on_done

    def run(self) -> None:
        try:
            self._worker.run()
        finally:
            try:
                from PyQt5.QtCore import QTimer
                from PyQt5.QtWidgets import QApplication

                app = QApplication.instance()
                if app is not None and callable(self._on_done):
                    QTimer.singleShot(0, self._on_done)
            except Exception:
                logger.exception("Render2DBatchChunkRunner completion notify failed")


class UniversalLoadWorker(QRunnable):
    def __init__(
        self,
        path,
        signals,
        batch_size=400,
        cancel_event: threading.Event | None = None,
        structure_choice_event: threading.Event | None = None,
    ):
        super().__init__()
        self.path, self.signals, self.batch_size = path, signals, batch_size
        self.cancel_event = cancel_event
        self.structure_choice_event = structure_choice_event

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _wait_for_structure_source_choice(self, headers: list[str]) -> bool:
        """Pause the reader until the UI picks a structure column (or load is cancelled)."""
        ev = self.structure_choice_event
        if ev is None or not needs_structure_source_picker(headers):
            return True
        ev.clear()
        try:
            self.signals.structure_source_probe.emit(list(headers))
        except Exception:
            logger.exception("structure_source_probe emit failed")
            ev.set()
            return True
        while True:
            if ev.wait(timeout=0.2):
                return True
            if self._cancelled():
                ev.set()
                return False

    def run(self):
        try:
            self.signals.tool_progress.emit("Reading file…", -1, -1)
        except Exception:
            pass
        ext = os.path.splitext(self.path)[1].lower()
        batch_size = self.batch_size
        cfg = load_config()
        text_first = bool(cfg.ingest_csv_text_first)
        try:
            headers = ["ID_HIDDEN", "Structure"]
            first_emit = True
            batch = []
            if ext in [".sdf", ".mol"]:
                suppl = Chem.SDMolSupplier(self.path)
                for mol in suppl:
                    if self._cancelled():
                        break
                    if mol:
                        if first_emit and len(batch) == 0:
                            headers.extend(sorted([str(p) for p in mol.GetPropNames()]))
                            if not self._wait_for_structure_source_choice(headers):
                                break
                        batch.append(mol)
                        if len(batch) >= batch_size:
                            self.signals.mols_loaded.emit(batch, headers if first_emit else [], first_emit, False)
                            first_emit = False
                            batch = []
            elif ext in [".smi", ".txt", ".csv"]:
                delim = "," if ext == ".csv" else "\t"
                with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                    if ext == ".smi" and text_first:
                        headers.append("SMILES")
                        if self._wait_for_structure_source_choice(headers):
                            for line in f:
                                if self._cancelled():
                                    break
                                cells = smi_line_to_cells(line)
                                if cells is None:
                                    continue
                                batch.append(cells)
                                if len(batch) >= batch_size:
                                    self.signals.mols_loaded.emit(
                                        batch, headers if first_emit else [], first_emit, False
                                    )
                                    first_emit = False
                                    batch = []
                    else:
                        reader = csv.DictReader(f, delimiter=delim)
                        fieldnames = list(reader.fieldnames or [])
                        smi_col = next(
                            (
                                fn
                                for fn in fieldnames
                                if fn.lower() in ["smiles", "smi", "structure", "mol"]
                            ),
                            fieldnames[0] if fieldnames else None,
                        )
                        if smi_col:
                            headers.append("SMILES")
                            headers.extend([h for h in fieldnames if h != smi_col])
                            if not self._wait_for_structure_source_choice(headers):
                                smi_col = None
                        for row in reader:
                            if self._cancelled():
                                break
                            if smi_col is None:
                                continue
                            if text_first:
                                cells = csv_row_to_cells(row, smi_col=smi_col, fieldnames=fieldnames)
                                if cells is None:
                                    continue
                                batch.append(cells)
                            else:
                                m = Chem.MolFromSmiles(row[smi_col])
                                if m:
                                    m.SetProp("SMILES", row[smi_col])
                                    for h in fieldnames:
                                        if h != smi_col:
                                            m.SetProp(h, str(row[h]))
                                    batch.append(m)
                            if len(batch) >= batch_size:
                                self.signals.mols_loaded.emit(
                                    batch, headers if first_emit else [], first_emit, False
                                )
                                first_emit = False
                                batch = []
            elif ext == ".tdt":
                suppl = Chem.TDTMolSupplier(self.path)
                for mol in suppl:
                    if self._cancelled():
                        break
                    if mol:
                        if first_emit and len(batch) == 0:
                            headers.extend(sorted([str(p) for p in mol.GetPropNames()]))
                            if not self._wait_for_structure_source_choice(headers):
                                break
                        batch.append(mol)
                        if len(batch) >= batch_size:
                            self.signals.mols_loaded.emit(batch, headers if first_emit else [], first_emit, False)
                            first_emit = False
                            batch = []
            elif ext == ".pdb":
                suppl = Chem.PDBMolSupplier(self.path)
                for mol in suppl:
                    if self._cancelled():
                        break
                    if mol:
                        batch.append(mol)
                        if len(batch) >= batch_size:
                            self.signals.mols_loaded.emit(batch, headers if first_emit else [], first_emit, False)
                            first_emit = False
                            batch = []

            # emit remaining
            if batch:
                self.signals.mols_loaded.emit(batch, headers if first_emit else [], first_emit, True)
            else:
                # if no molecules found, still signal completion
                if first_emit:
                    self.signals.mols_loaded.emit([], headers, True, True)
                else:
                    self.signals.mols_loaded.emit([], [], False, True)
        except Exception:
            logger.exception("UniversalLoadWorker failed")
            try:
                self.signals.mols_loaded.emit([], ["ID_HIDDEN", "Structure"], True, True)
            except Exception:
                logger.warning("UniversalLoadWorker: failed to emit empty completion signal", exc_info=True)


class RenderWorker(QRunnable):
    def __init__(
        self,
        idx,
        mol,
        signals,
        width=STRUCTURE_DEPICT_WIDTH,
        height=STRUCTURE_DEPICT_HEIGHT,
        props=None,
        cancel_event: threading.Event | None = None,
        skip_mol_props: bool = False,
        render_batch_session: int = 0,
    ):
        super().__init__()
        self.idx, self.mol, self.signals, self.w, self.h, self.props = idx, mol, signals, width, height, props
        self.cancel_event = cancel_event
        self.skip_mol_props = skip_mol_props
        self.render_batch_session = int(render_batch_session or 0)

    def run(self):
        sid = self.render_batch_session
        if self.cancel_event is not None and self.cancel_event.is_set():
            try:
                self.signals.rendered.emit(self.idx, {}, b"", False, self.w, self.h, sid)
            except Exception:
                pass
            return
        try:
            if self.skip_mol_props:
                p = {}
            elif self.props is not None:
                p = self.props
            else:
                p = {n: safe_mol_prop_string(self.mol, n) for n in self.mol.GetPropNames()}
            png = render_molecule_png(self.mol, int(self.w), int(self.h))
            self.signals.rendered.emit(self.idx, p, png, True, self.w, self.h, sid)
        except Exception:
            self.signals.rendered.emit(self.idx, {}, b"", False, self.w, self.h, sid)


class WashWorker(QRunnable):
    def __init__(self, mols_data, signals, is_smiles: bool = False, cancel_event: threading.Event | None = None):
        super().__init__()
        self.mols_data, self.signals, self.is_smiles = mols_data, signals, is_smiles
        self.cancel_event = cancel_event

    def run(self):
        items = list(self.mols_data)
        total = max(len(items), 1)
        res = []
        done_count = 0
        cancelled = False
        for done, row in enumerate(items, start=1):
            if self.cancel_event is not None and self.cancel_event.is_set():
                cancelled = True
                break
            done_count = done
            mol = None
            source_text: str | None = None
            i = row[0]
            if self.is_smiles:
                source_text = str(row[1] or "").strip()
                mol = parse_molecule_from_cell_text(source_text) if source_text else None
            elif len(row) >= 3:
                mol = row[1]
                source_text = (str(row[2]).strip() if row[2] else None) or None
            else:
                mol = row[1]
            if mol is None and source_text:
                mol = parse_molecule_from_cell_text(source_text)
            if mol is None:
                try:
                    self.signals.tool_progress.emit("Disconnect fragments…", done, total)
                except Exception:
                    pass
                continue
            parent, fragments = largest_fragment_and_rest(mol, source_text)
            if parent is None:
                try:
                    self.signals.tool_progress.emit("Disconnect fragments…", done, total)
                except Exception:
                    pass
                continue
            res.append((i, parent, fragments))
            try:
                self.signals.tool_progress.emit("Disconnect fragments…", done, total)
            except Exception:
                pass
        emit_partial_results_if_cancelled(
            self.signals, "Disconnect fragments", done_count, total, cancelled
        )
        self.signals.washed.emit(res)


class NeutralizeWorker(QRunnable):
    """Neutralize each structure in ``mols_data`` (``(oid, mol)`` or ``(oid, cell_text)`` when *is_smiles*)."""

    def __init__(self, mols_data, signals, is_smiles: bool = False, cancel_event: threading.Event | None = None):
        super().__init__()
        self.mols_data, self.signals, self.is_smiles = mols_data, signals, is_smiles
        self.cancel_event = cancel_event

    def run(self):
        items = list(self.mols_data)
        total = max(len(items), 1)
        res: list[tuple[int, object]] = []
        done_count = 0
        cancelled = False
        for done, row in enumerate(items, start=1):
            if self.cancel_event is not None and self.cancel_event.is_set():
                cancelled = True
                break
            done_count = done
            oid = row[0]
            if self.is_smiles:
                raw = str(row[1] or "").strip()
                mol = parse_molecule_from_cell_text(raw) if raw else None
            else:
                mol = row[1]
            if mol is None:
                try:
                    self.signals.tool_progress.emit("Neutralize…", done, total)
                except Exception:
                    pass
                continue
            neutral = neutralize_mol(mol)
            if neutral is not None:
                res.append((oid, neutral))
            try:
                self.signals.tool_progress.emit("Neutralize…", done, total)
            except Exception:
                pass
        emit_partial_results_if_cancelled(
            self.signals, "Neutralize", done_count, total, cancelled
        )
        self.signals.neutralized.emit(res)


class AddExplicitHydrogensWorker(QRunnable):
    """Expand implicit hydrogens to explicit atoms for each structure in ``mols_data``."""

    def __init__(self, mols_data, signals, is_smiles: bool = False, cancel_event: threading.Event | None = None):
        super().__init__()
        self.mols_data, self.signals, self.is_smiles = mols_data, signals, is_smiles
        self.cancel_event = cancel_event

    def run(self):
        items = list(self.mols_data)
        total = max(len(items), 1)
        res: list[tuple[int, object]] = []
        done_count = 0
        cancelled = False
        for done, row in enumerate(items, start=1):
            if self.cancel_event is not None and self.cancel_event.is_set():
                cancelled = True
                break
            done_count = done
            oid = row[0]
            if self.is_smiles:
                raw = str(row[1] or "").strip()
                mol = parse_molecule_from_cell_text(raw) if raw else None
            else:
                mol = row[1]
            if mol is None:
                try:
                    self.signals.tool_progress.emit("Add explicit hydrogens…", done, total)
                except Exception:
                    pass
                continue
            with_h = add_explicit_hydrogens(mol)
            if with_h is not None:
                res.append((oid, with_h))
            try:
                self.signals.tool_progress.emit("Add explicit hydrogens…", done, total)
            except Exception:
                pass
        emit_partial_results_if_cancelled(
            self.signals, "Add explicit hydrogens", done_count, total, cancelled
        )
        self.signals.explicit_hydrogens_added.emit(res)


class RemoveExplicitHydrogensWorker(QRunnable):
    """Remove explicit hydrogen atoms from each structure in ``mols_data``."""

    def __init__(self, mols_data, signals, is_smiles: bool = False, cancel_event: threading.Event | None = None):
        super().__init__()
        self.mols_data, self.signals, self.is_smiles = mols_data, signals, is_smiles
        self.cancel_event = cancel_event

    def run(self):
        items = list(self.mols_data)
        total = max(len(items), 1)
        res: list[tuple[int, object]] = []
        done_count = 0
        cancelled = False
        for done, row in enumerate(items, start=1):
            if self.cancel_event is not None and self.cancel_event.is_set():
                cancelled = True
                break
            done_count = done
            oid = row[0]
            if self.is_smiles:
                raw = str(row[1] or "").strip()
                mol = parse_molecule_from_cell_text(raw) if raw else None
            else:
                mol = row[1]
            if mol is None:
                try:
                    self.signals.tool_progress.emit("Remove explicit hydrogens…", done, total)
                except Exception:
                    pass
                continue
            stripped = remove_explicit_hydrogens(mol)
            if stripped is not None:
                res.append((oid, stripped))
            try:
                self.signals.tool_progress.emit("Remove explicit hydrogens…", done, total)
            except Exception:
                pass
        emit_partial_results_if_cancelled(
            self.signals, "Remove explicit hydrogens", done_count, total, cancelled
        )
        self.signals.explicit_hydrogens_removed.emit(res)

