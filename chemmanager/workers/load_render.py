"""File load, 2D render, and fragment wash workers."""

import csv
import logging
import os
import threading
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from PyQt5.QtCore import QRunnable
from rdkit import Chem

from rdkit.Chem.Draw import rdMolDraw2D

from ..display_constants import STRUCTURE_DEPICT_HEIGHT, STRUCTURE_DEPICT_WIDTH
from ..utils import mol_to_canonical_smiles, parse_molecule_from_cell_text, safe_mol_prop_string
from .signals import WorkerSignals

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
        d = rdMolDraw2D.MolDraw2DCairo(int(w), int(h))
        rdMolDraw2D.PrepareAndDrawMolecule(d, mol)
        d.FinishDrawing()
        return int(oid), p, d.GetDrawingText(), True, int(w), int(h), sid
    except Exception:
        return int(oid), {}, b"", False, int(w), int(h), sid


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
        ex = ProcessPoolExecutor(max_workers=proc_workers)

        def _fill() -> None:
            while len(pending) < max_inflight:
                t = next(it, None)
                if t is None:
                    break
                pending.add(ex.submit(_mp_render_structure_row, t))

        try:
            _fill()
            while pending:
                if ev is not None and ev.is_set():
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
            try:
                ex.shutdown(wait=not user_cancelled, cancel_futures=True)
            except TypeError:
                ex.shutdown(wait=not user_cancelled)


class UniversalLoadWorker(QRunnable):
    def __init__(self, path, signals, batch_size=400, cancel_event: threading.Event | None = None):
        super().__init__()
        self.path, self.signals, self.batch_size = path, signals, batch_size
        self.cancel_event = cancel_event

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

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
                suppl = Chem.SDMolSupplier(self.path)
                for mol in suppl:
                    if self._cancelled():
                        break
                    if mol:
                        if first_emit and len(batch) == 0:
                            headers.extend(sorted([str(p) for p in mol.GetPropNames()]))
                        batch.append(mol)
                        if len(batch) >= batch_size:
                            self.signals.mols_loaded.emit(batch, headers if first_emit else [], first_emit, False)
                            first_emit = False
                            batch = []
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
                                self.signals.mols_loaded.emit(batch, headers if first_emit else [], first_emit, False)
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
        except Exception as e:
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
        res = []
        for done, (i, item) in enumerate(items, start=1):
            if self.cancel_event is not None and self.cancel_event.is_set():
                break
            mol = None
            if self.is_smiles:
                smi = str(item or "").strip()
                mol = parse_molecule_from_cell_text(smi) if smi else None
            else:
                mol = item
            if mol is None:
                try:
                    self.signals.tool_progress.emit("Disconnect fragments…", done, total)
                except Exception:
                    pass
                continue
            f = sorted(Chem.GetMolFrags(mol, asMols=True), key=lambda x: x.GetNumAtoms(), reverse=True)
            if not f:
                try:
                    self.signals.tool_progress.emit("Disconnect fragments…", done, total)
                except Exception:
                    pass
                continue
            parent = f[0]
            fragments = " . ".join([mol_to_canonical_smiles(s) for s in f[1:]])
            res.append((i, parent, fragments))
            try:
                self.signals.tool_progress.emit("Disconnect fragments…", done, total)
            except Exception:
                pass
        self.signals.washed.emit(res)

