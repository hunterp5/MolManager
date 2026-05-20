"""BRICS / RECAP fragment decomposition workers (Tools menu)."""

from __future__ import annotations

import threading

from PyQt5.QtCore import QRunnable
from rdkit import Chem

from ..fragment_decomposition import (
    DecompositionMethod,
    assemble_fragment_table_rows,
    decompose_fragments,
)
from .signals import WorkerSignals


class FragmentDecompositionWorker(QRunnable):
    """Decompose each structure into fragments and emit new table columns."""

    def __init__(
        self,
        data: list[tuple[int, Chem.Mol]],
        method: DecompositionMethod,
        column_prefix: str,
        tool_title: str,
        signals: WorkerSignals,
        cancel_event: threading.Event | None = None,
    ):
        super().__init__()
        self.data = data
        self.method = method
        self.column_prefix = (column_prefix or "").strip()
        self.tool_title = tool_title
        self.signals = signals
        self.cancel_event = cancel_event

    def run(self) -> None:
        ev = self.cancel_event
        if ev is not None and ev.is_set():
            return

        oids: list[int] = []
        per_row: list[list[str]] = []
        tot = max(len(self.data), 1)
        label = f"{self.tool_title}…"
        try:
            self.signals.tool_progress.emit(label, 0, tot)
        except Exception:
            pass

        for done, (oid, mol) in enumerate(self.data, start=1):
            if ev is not None and ev.is_set():
                return
            oids.append(int(oid))
            try:
                per_row.append(decompose_fragments(mol, self.method))
            except Exception as exc:
                try:
                    self.signals.fragment_decomp_failed.emit(
                        str(exc) or exc.__class__.__name__,
                        self.tool_title,
                    )
                except Exception:
                    pass
                return
            try:
                self.signals.tool_progress.emit(label, done, tot)
            except Exception:
                pass

        table_rows, headers = assemble_fragment_table_rows(oids, per_row, self.column_prefix)
        if not headers:
            try:
                self.signals.fragment_decomp_failed.emit(
                    "No fragments were produced for any row in scope.",
                    self.tool_title,
                )
            except Exception:
                pass
            return

        try:
            self.signals.tool_progress.emit(label, tot, tot)
        except Exception:
            pass
        try:
            self.signals.fragment_decomp_finished.emit(table_rows, headers, self.tool_title)
        except Exception:
            pass
