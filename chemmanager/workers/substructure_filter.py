"""Substructure filter batch worker."""

import logging

from PyQt5.QtCore import QRunnable
from rdkit import Chem

from .signals import SubstructureFilterSignals

logger = logging.getLogger(__name__)


class SubstructureFilterWorker(QRunnable):
    """Compute substructure matches off the UI thread (SMILES per row vs SMARTS query)."""

    def __init__(self, job_gen: int, smarts: str, targets: list[tuple[int, str]], signals: SubstructureFilterSignals):
        super().__init__()
        self.job_gen = job_gen
        self.smarts = smarts
        self.targets = targets
        self.signals = signals

    def run(self):
        try:
            s = (self.smarts or "").strip()
            if not s:
                self.signals.finished.emit(self.job_gen, frozenset())
                return
            q = Chem.MolFromSmarts(s)
            if q is None:
                self.signals.finished.emit(self.job_gen, frozenset())
                return
            matched: set[int] = set()
            for oid, smi in self.targets:
                smi = (smi or "").strip()
                if not smi:
                    continue
                try:
                    m = Chem.MolFromSmiles(smi)
                    if m is not None and m.HasSubstructMatch(q):
                        matched.add(int(oid))
                except Exception:
                    continue
            self.signals.finished.emit(self.job_gen, frozenset(matched))
        except Exception as e:
            logger.exception("SubstructureFilterWorker failed")
            self.signals.failed.emit(self.job_gen, str(e))

