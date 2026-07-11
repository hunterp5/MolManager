"""BRICS / RECAP fragment recomposition workers (Tools menu)."""

from __future__ import annotations

import threading

from PyQt5.QtCore import QRunnable

from ..fragment_decomposition import RecompositionMethod, recompose_fragments
from ..fragment_recomposition_filters import filter_product_smiles
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
        *,
        output_filters: str = "",
        cancel_event: threading.Event | None = None,
        progress_state=None,
    ):
        super().__init__()
        self.fragment_smiles = list(fragment_smiles)
        self.method = method
        self.max_depth = int(max_depth)
        self.max_products = int(max_products)
        self.output_filters = str(output_filters or "")
        self.tool_title = tool_title
        self.signals = signals
        self.cancel_event = cancel_event
        self.progress_state = progress_state

    def run(self) -> None:
        ev = self.cancel_event
        if ev is not None and ev.is_set():
            return
        from ..tool_progress import report_tool_progress

        label = self.tool_title
        report_tool_progress(
            message=label,
            done=0,
            total=1,
            progress_state=self.progress_state,
            signals=self.signals,
            force_signal=True,
        )
        if ev is not None and ev.is_set():
            return
        try:
            products = recompose_fragments(
                self.fragment_smiles,
                self.method,
                max_depth=self.max_depth,
                max_products=self.max_products,
            )
            filtered_out = 0
            if self.output_filters.strip():
                products, filtered_out = filter_product_smiles(products, self.output_filters)
                if not products:
                    raise ValueError(
                        "No products passed the output filters. "
                        "Relax the property limits or increase max products."
                    )
        except ValueError as exc:
            try:
                self.signals.fragment_recomp_failed.emit(
                    str(exc) or exc.__class__.__name__,
                    self.tool_title,
                )
            except Exception:
                pass
            return
        except Exception as exc:
            try:
                self.signals.fragment_recomp_failed.emit(
                    str(exc) or exc.__class__.__name__,
                    self.tool_title,
                )
            except Exception:
                pass
            return
        report_tool_progress(
            message=label,
            done=1,
            total=1,
            progress_state=self.progress_state,
            signals=self.signals,
            force_signal=True,
        )
        try:
            self.signals.fragment_recomp_finished.emit(products, self.tool_title, filtered_out)
        except Exception:
            pass
