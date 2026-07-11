"""File load, 2D render, and fragment wash workers."""

import csv
import logging
import os
import threading
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from .process_pool_utils import (
    register_process_pool,
    should_terminate_process_pool,
    shutdown_process_pool_executor,
)

from PyQt5.QtCore import QRunnable
from rdkit import Chem

from rdkit.Chem.Draw import rdMolDraw2D

from ..config import load_config
from ..display_constants import STRUCTURE_DEPICT_HEIGHT, STRUCTURE_DEPICT_WIDTH
from ..import_structure import needs_structure_source_picker
from ..ingest_cells import prepare_mol_row
from ..fragment_disconnect import largest_fragment_and_rest
from ..prepare_structures_parallel import (
    _mp_add_explicit_h_row,
    _mp_add_explicit_h_smiles_row,
    _mp_neutralize_mol_row,
    _mp_neutralize_smiles_row,
    _mp_wash_mol_row,
    _mp_wash_smiles_row,
    mol_from_bytes,
    run_prepare_tasks_parallel_ordered,
    should_use_prepare_structures_process_pool,
)
from ..sdf_parallel import iter_sdf_molblocks, mp_parse_sdf_molblocks
from ..structure_neutralize import neutralize_mol
from ..structure_hydrogens import add_explicit_hydrogens
from ..utils import parse_molecule_from_cell_text, safe_mol_prop_string
from .signals import WorkerSignals, emit_partial_results_if_cancelled

logger = logging.getLogger(__name__)


def _prepare_row_mol(payload) -> Chem.Mol | None:
    if isinstance(payload, bytes):
        return mol_from_bytes(payload)
    return payload


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
        d = rdMolDraw2D.MolDraw2DCairo(int(w), int(h))
        rdMolDraw2D.PrepareAndDrawMolecule(d, mol)
        d.FinishDrawing()
        return int(oid), p, d.GetDrawingText(), True, int(w), int(h), sid
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
        from ..workers.process_pool_utils import application_is_shutting_down

        if application_is_shutting_down():
            return
        done_ev = getattr(self._app, "_render2d_batch_done_event", None)
        if done_ev is not None:
            done_ev.clear()
        prep_done = threading.Event()
        try:
            from PyQt5.QtCore import QMetaObject, Qt

            self._app._render2d_queue_payload = self._payload
            self._app._render2d_queue_cancel_event = self._cancel_event
            self._app._render2d_queue_prep_done = prep_done
            if application_is_shutting_down():
                return
            # QueuedConnection avoids closeEvent deadlocks (GUI blocked in shutdown wait).
            QMetaObject.invokeMethod(
                self._app,
                "_begin_render2d_batch_from_queue",
                Qt.QueuedConnection,
            )
            while not prep_done.is_set():
                if prep_done.wait(timeout=0.25):
                    break
                if self._cancel_event is not None and self._cancel_event.is_set():
                    break
                if application_is_shutting_down():
                    break
        except Exception:
            logger.exception("Render2D batch UI prepare failed")
            if done_ev is not None:
                done_ev.set()
            return
        finally:
            self._app._render2d_queue_prep_done = None
        if done_ev is not None:
            if not getattr(self._app, "render2d_batch_active", lambda: False)():
                done_ev.set()
            else:
                while not done_ev.is_set():
                    if done_ev.wait(timeout=0.25):
                        break
                    if self._cancel_event is not None and self._cancel_event.is_set():
                        break
                    if application_is_shutting_down():
                        break
                done_ev.set()


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


class UniversalLoadWorker(QRunnable):
    def __init__(
        self,
        path,
        signals,
        batch_size=400,
        cancel_event: threading.Event | None = None,
        structure_choice_event: threading.Event | None = None,
        structure_override_holder: list | None = None,
    ):
        super().__init__()
        self.path, self.signals, self.batch_size = path, signals, batch_size
        self.cancel_event = cancel_event
        self.structure_choice_event = structure_choice_event
        self._structure_override_holder = structure_override_holder

    def _structure_field(self) -> str | None:
        holder = self._structure_override_holder
        if not holder:
            return None
        try:
            field = holder[0]
        except (IndexError, TypeError):
            return None
        return field if isinstance(field, str) and field.strip() else None

    def _emit_batch(self, batch: list, headers: list[str], first_emit: bool, is_last: bool) -> None:
        data_headers = headers[2:] if len(headers) > 2 else []
        field = self._structure_field()
        payload: list[tuple] = []
        for mol in batch:
            mol_out, cells = prepare_mol_row(mol, data_headers, field)
            payload.append((mol_out, cells))
        self.signals.mols_loaded.emit(payload, headers if first_emit else [], first_emit, is_last)

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _should_parallel_sdf(self, path: str) -> bool:
        cfg = load_config()
        if cfg.ingest_sdf_parallel_min_bytes <= 0:
            return False
        if (os.cpu_count() or 1) <= 1:
            return False
        try:
            return os.path.getsize(path) >= int(cfg.ingest_sdf_parallel_min_bytes)
        except OSError:
            return False

    def _extend_headers_from_mol(self, mol, headers: list[str]) -> bool:
        """Add SD-tag headers from the first parsed mol; wait for structure-column choice."""
        if not mol:
            return True
        existing = set(headers)
        for prop in sorted(mol.GetPropNames(), key=str):
            name = str(prop)
            if name not in existing:
                headers.append(name)
                existing.add(name)
        return self._wait_for_structure_source_choice(headers)

    def _append_parsed_mols(
        self,
        blobs: list[bytes | None],
        batch: list,
        headers: list[str],
        first_emit: bool,
        batch_size: int,
    ) -> tuple[list, bool, bool]:
        """Hydrate pickled mols, extend *batch*, and emit when full."""
        for blob in blobs:
            if self._cancelled():
                return batch, first_emit, True
            if not blob:
                continue
            try:
                mol = Chem.Mol(blob)
            except Exception:
                continue
            if first_emit and not batch:
                if not self._extend_headers_from_mol(mol, headers):
                    return batch, first_emit, True
            batch.append(mol)
            if len(batch) >= batch_size:
                self._emit_batch(batch, headers, first_emit, False)
                first_emit = False
                batch = []
        return batch, first_emit, False

    def _load_sdf_parallel(self, path: str, batch_size: int) -> tuple[list, list[str], bool]:
        cfg = load_config()
        parse_chunk = int(cfg.ingest_sdf_parse_chunk)
        ncpu = os.cpu_count() or 4
        proc_workers = min(int(cfg.ingest_sdf_parallel_workers), max(2, ncpu - 1))
        max_inflight = max(proc_workers * 2, proc_workers + 1)

        headers = ["ID_HIDDEN", "Structure"]
        first_emit = True
        batch: list = []
        block_buf: list[str] = []
        futures: deque = deque()
        ex = register_process_pool(ProcessPoolExecutor(max_workers=proc_workers))
        stopped = False
        try:
            for block in iter_sdf_molblocks(path):
                if self._cancelled():
                    stopped = True
                    break
                block_buf.append(block)
                if len(block_buf) < parse_chunk:
                    continue
                futures.append(ex.submit(mp_parse_sdf_molblocks, block_buf))
                block_buf = []
                while len(futures) >= max_inflight:
                    batch, first_emit, stopped = self._append_parsed_mols(
                        futures.popleft().result(), batch, headers, first_emit, batch_size
                    )
                    if stopped:
                        break
                if stopped:
                    break
            if not stopped and block_buf:
                futures.append(ex.submit(mp_parse_sdf_molblocks, block_buf))
            while not stopped and futures:
                batch, first_emit, stopped = self._append_parsed_mols(
                    futures.popleft().result(), batch, headers, first_emit, batch_size
                )
        finally:
            shutdown_process_pool_executor(
                ex, kill_workers=should_terminate_process_pool(self.cancel_event)
            )
        return batch, headers, first_emit

    def _load_sdf_sequential(self, path: str, batch_size: int) -> tuple[list, list[str], bool]:
        headers = ["ID_HIDDEN", "Structure"]
        first_emit = True
        batch: list = []
        suppl = Chem.SDMolSupplier(path)
        for mol in suppl:
            if self._cancelled():
                break
            if not mol:
                continue
            if first_emit and not batch:
                if not self._extend_headers_from_mol(mol, headers):
                    break
            batch.append(mol)
            if len(batch) >= batch_size:
                self._emit_batch(batch, headers, first_emit, False)
                first_emit = False
                batch = []
        return batch, headers, first_emit

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
        try:
            headers = ["ID_HIDDEN", "Structure"]
            first_emit = True
            batch = []
            if ext in [".sdf", ".mol"]:
                if ext == ".sdf" and self._should_parallel_sdf(self.path):
                    batch, headers, first_emit = self._load_sdf_parallel(self.path, batch_size)
                else:
                    batch, headers, first_emit = self._load_sdf_sequential(self.path, batch_size)
            elif ext in [".smi", ".txt", ".csv"]:
                delim = "," if ext == ".csv" else "\t"
                with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f, delimiter=delim)
                    fieldnames = reader.fieldnames or []
                    smi_col = next(
                        (fn for fn in fieldnames if fn.lower() in ["smiles", "smi", "structure", "mol"]),
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
                        m = Chem.MolFromSmiles(row[smi_col])
                        if m:
                            m.SetProp("SMILES", row[smi_col])
                            for h in fieldnames:
                                if h != smi_col:
                                    m.SetProp(h, str(row[h]))
                            batch.append(m)
                            if len(batch) >= batch_size:
                                self._emit_batch(batch, headers, first_emit, False)
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
                            self._emit_batch(batch, headers, first_emit, False)
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
                            self._emit_batch(batch, headers, first_emit, False)
                            first_emit = False
                            batch = []

            # emit remaining
            if batch:
                self._emit_batch(batch, headers, first_emit, True)
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
            d = rdMolDraw2D.MolDraw2DCairo(self.w, self.h)
            rdMolDraw2D.PrepareAndDrawMolecule(d, self.mol)
            d.FinishDrawing()
            self.signals.rendered.emit(self.idx, p, d.GetDrawingText(), True, self.w, self.h, sid)
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
        cfg = load_config()
        min_rows = int(cfg.prepare_structures_process_pool_min_rows)
        use_mp = should_use_prepare_structures_process_pool(total, min_rows=min_rows)
        res = []
        done_count = 0
        cancelled = False

        def _emit_progress(done: int, _total: int = 0) -> None:
            try:
                self.signals.tool_progress.emit("Disconnect fragments…", done, total)
            except Exception:
                pass

        if use_mp:
            if self.is_smiles:
                tasks = [(int(row[0]), str(row[1] or "")) for row in items]
                mp_rows = run_prepare_tasks_parallel_ordered(
                    tasks,
                    _mp_wash_smiles_row,
                    cancel_event=self.cancel_event,
                    on_progress=_emit_progress,
                )
                for oid, parent_bytes, fragments in mp_rows:
                    parent = mol_from_bytes(parent_bytes)
                    if parent is not None:
                        res.append((oid, parent, fragments))
                done_count = len(items)
                cancelled = should_terminate_process_pool(self.cancel_event)
            else:
                tasks = []
                for row in items:
                    oid = int(row[0])
                    payload = row[1]
                    source_text = (str(row[2]).strip() if len(row) >= 3 and row[2] else None) or None
                    if isinstance(payload, bytes):
                        blob = payload
                    else:
                        mol = payload
                        if mol is None:
                            continue
                        try:
                            blob = mol.ToBinary()
                        except Exception:
                            blob = None
                    if blob:
                        tasks.append((oid, blob, source_text))
                mp_rows = run_prepare_tasks_parallel_ordered(
                    tasks,
                    _mp_wash_mol_row,
                    cancel_event=self.cancel_event,
                    on_progress=_emit_progress,
                )
                for oid, parent_bytes, fragments in mp_rows:
                    parent = mol_from_bytes(parent_bytes)
                    if parent is not None:
                        res.append((oid, parent, fragments))
                done_count = len(items)
                cancelled = should_terminate_process_pool(self.cancel_event)
        else:
            for done, row in enumerate(items, start=1):
                if should_terminate_process_pool(self.cancel_event):
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
                    mol = _prepare_row_mol(row[1])
                    source_text = (str(row[2]).strip() if row[2] else None) or None
                else:
                    mol = _prepare_row_mol(row[1])
                if mol is None and source_text:
                    mol = parse_molecule_from_cell_text(source_text)
                if mol is None:
                    _emit_progress(done)
                    continue
                parent, fragments = largest_fragment_and_rest(mol, source_text)
                if parent is None:
                    _emit_progress(done)
                    continue
                res.append((i, parent, fragments))
                _emit_progress(done)
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
        cfg = load_config()
        min_rows = int(cfg.prepare_structures_process_pool_min_rows)
        use_mp = should_use_prepare_structures_process_pool(total, min_rows=min_rows)
        res: list[tuple[int, object]] = []
        done_count = 0
        cancelled = False

        def _emit_progress(done: int, _total: int = 0) -> None:
            try:
                self.signals.tool_progress.emit("Neutralize…", done, total)
            except Exception:
                pass

        if use_mp:
            if self.is_smiles:
                tasks = [(int(row[0]), str(row[1] or "")) for row in items]
                mp_rows = run_prepare_tasks_parallel_ordered(
                    tasks,
                    _mp_neutralize_smiles_row,
                    cancel_event=self.cancel_event,
                    on_progress=_emit_progress,
                )
            else:
                tasks = []
                for row in items:
                    payload = row[1]
                    if isinstance(payload, bytes):
                        blob = payload
                    else:
                        mol = payload
                        if mol is None:
                            continue
                        try:
                            blob = mol.ToBinary()
                        except Exception:
                            blob = None
                    if blob:
                        tasks.append((int(row[0]), blob))
                mp_rows = run_prepare_tasks_parallel_ordered(
                    tasks,
                    _mp_neutralize_mol_row,
                    cancel_event=self.cancel_event,
                    on_progress=_emit_progress,
                )
            for oid, out_bytes in mp_rows:
                mol = mol_from_bytes(out_bytes)
                if mol is not None:
                    res.append((oid, mol))
            done_count = len(items)
            cancelled = should_terminate_process_pool(self.cancel_event)
        else:
            for done, row in enumerate(items, start=1):
                if should_terminate_process_pool(self.cancel_event):
                    cancelled = True
                    break
                done_count = done
                oid = row[0]
                if self.is_smiles:
                    raw = str(row[1] or "").strip()
                    mol = parse_molecule_from_cell_text(raw) if raw else None
                else:
                    mol = _prepare_row_mol(row[1])
                if mol is None:
                    _emit_progress(done)
                    continue
                neutral = neutralize_mol(mol)
                if neutral is not None:
                    res.append((oid, neutral))
                _emit_progress(done)
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
        cfg = load_config()
        min_rows = int(cfg.prepare_structures_process_pool_min_rows)
        use_mp = should_use_prepare_structures_process_pool(total, min_rows=min_rows)
        res: list[tuple[int, object]] = []
        done_count = 0
        cancelled = False

        def _emit_progress(done: int, _total: int = 0) -> None:
            try:
                self.signals.tool_progress.emit("Add explicit hydrogens…", done, total)
            except Exception:
                pass

        if use_mp:
            if self.is_smiles:
                tasks = [(int(row[0]), str(row[1] or "")) for row in items]
                mp_rows = run_prepare_tasks_parallel_ordered(
                    tasks,
                    _mp_add_explicit_h_smiles_row,
                    cancel_event=self.cancel_event,
                    on_progress=_emit_progress,
                )
            else:
                tasks = []
                for row in items:
                    payload = row[1]
                    if isinstance(payload, bytes):
                        blob = payload
                    else:
                        mol = payload
                        if mol is None:
                            continue
                        try:
                            blob = mol.ToBinary()
                        except Exception:
                            blob = None
                    if blob:
                        tasks.append((int(row[0]), blob))
                mp_rows = run_prepare_tasks_parallel_ordered(
                    tasks,
                    _mp_add_explicit_h_row,
                    cancel_event=self.cancel_event,
                    on_progress=_emit_progress,
                )
            for oid, out_bytes in mp_rows:
                mol = mol_from_bytes(out_bytes)
                if mol is not None:
                    res.append((oid, mol))
            done_count = len(items)
            cancelled = should_terminate_process_pool(self.cancel_event)
        else:
            for done, row in enumerate(items, start=1):
                if should_terminate_process_pool(self.cancel_event):
                    cancelled = True
                    break
                done_count = done
                oid = row[0]
                if self.is_smiles:
                    raw = str(row[1] or "").strip()
                    mol = parse_molecule_from_cell_text(raw) if raw else None
                else:
                    mol = _prepare_row_mol(row[1])
                if mol is None:
                    _emit_progress(done)
                    continue
                with_h = add_explicit_hydrogens(mol)
                if with_h is not None:
                    res.append((oid, with_h))
                _emit_progress(done)
        emit_partial_results_if_cancelled(
            self.signals, "Add explicit hydrogens", done_count, total, cancelled
        )
        self.signals.explicit_hydrogens_added.emit(res)

