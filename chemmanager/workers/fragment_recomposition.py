"""BRICS / RECAP fragment recomposition workers (Tools menu)."""

from __future__ import annotations

import threading

from PyQt5.QtCore import QRunnable

from ..fragment_decomposition import RecompositionMethod, recompose_fragments
from .signals import WorkerSignals


class FragmentRecompositionWorker(QRunnable):
    """Build new molecules from pooled fragment SMILES columns."""

    def __init__(
        self,
        fragment_smiles: list[str],
        method: RecompositionMethod,
        max_depth: int,
        max_products: int,
        tool_title: str,
        signals: WorkerSignals,
        cancel_event: threading.Event | None = None,
    ):
        super().__init__()
        self.fragment_smiles = list(fragment_smiles)
        self.method = method
        self.max_depth = int(max_depth)
        self.max_products = int(max_products)
        self.tool_title = tool_title
        self.signals = signals
        self.cancel_event = cancel_event

    def run(self) -> None:
        ev = self.cancel_event
        if ev is not None and ev.is_set():
            return
        label = f"{self.tool_title}…"
        try:
            self.signals.tool_progress.emit(label, 0, 1)
        except Exception:
            pass
        if ev is not None and ev.is_set():
            return
        try:
            products = recompose_fragments(
                self.fragment_smiles,
                self.method,
                max_depth=self.max_depth,
                max_products=self.max_products,
            )
        except Exception as exc:
            try:
                self.signals.fragment_recomp_failed.emit(
                    str(exc) or exc.__class__.__name__,
                    self.tool_title,
                )
            except Exception:
                pass
            return
        try:
            self.signals.tool_progress.emit(label, 1, 1)
        except Exception:
            pass
        try:
            self.signals.fragment_recomp_finished.emit(products, self.tool_title)
        except Exception:
            pass
