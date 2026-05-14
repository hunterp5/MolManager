"""Fingerprint similarity batch worker."""

import logging
import os
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from PyQt5.QtCore import QRunnable
from rdkit import Chem
from rdkit import DataStructs
from rdkit.Chem import AllChem, rdMolDescriptors, rdmolops

from ..utils import mol_to_canonical_smiles
from .signals import FPSimilaritySignals

logger = logging.getLogger(__name__)


def _rdk_fingerprint_bitvect(mol: Chem.Mol, max_path: int = 5, fp_size: int = 2048):
    """RDKit topological path fingerprint (ExplicitBitVect), compatible across RDKit versions."""
    getter = getattr(AllChem, "GetRDKFingerprint", None)
    if getter is not None:
        return getter(mol, maxPath=max_path, fpSize=fp_size)
    return rdmolops.RDKFingerprint(mol, minPath=1, maxPath=max_path, fpSize=fp_size)


def fingerprint_bitvect_for_ui_choice(mol: Chem.Mol, fp_choice: str):
    """Bit vector for Morgan / RDK / MACCS strings used in the Fingerprint Similarity dialog."""
    try:
        if fp_choice.startswith("Morgan"):
            r, n = 2, 1024
            return AllChem.GetMorganFingerprintAsBitVect(mol, r, nBits=n)
        if fp_choice.startswith("RDK"):
            n = 2048
            return _rdk_fingerprint_bitvect(mol, max_path=5, fp_size=n)
        if fp_choice.startswith("MACCS"):
            return rdMolDescriptors.GetMACCSKeysFingerprint(mol)
    except Exception:
        return None
    return None


def _rdk_fp_onbits(mol: Chem.Mol, nbits: int) -> int:
    fp = _rdk_fingerprint_bitvect(mol, max_path=5, fp_size=nbits)
    if hasattr(fp, "GetNumOnBits"):
        return fp.GetNumOnBits()
    return int(sum(fp))


class FPSimilarityWorker(QRunnable):
    """Compute Tanimoto vs a fixed query fingerprint off the UI thread."""

    def __init__(
        self,
        qfp,
        targets: list[tuple[int, Chem.Mol]],
        fp_choice: str,
        signals: FPSimilaritySignals,
        cancel_event: threading.Event | None = None,
    ):
        super().__init__()
        self.qfp = qfp
        self.targets = targets
        self.fp_choice = fp_choice
        self.signals = signals
        self.cancel_event = cancel_event

    def run(self):
        try:
            cancel_ev = self.cancel_event

            def _one(args):
                oid, mol, qfp, fp_choice = args
                try:
                    fp = fingerprint_bitvect_for_ui_choice(mol, fp_choice)
                    if fp is None:
                        return None
                    sim = DataStructs.TanimotoSimilarity(qfp, fp)
                    return (oid, sim, mol_to_canonical_smiles(mol))
                except Exception:
                    return None

            n = len(self.targets)
            if n <= 0:
                self.signals.finished.emit([])
                return
            max_workers = min(8, max(1, (os.cpu_count() or 4)))
            tasks = [(oid, mol, self.qfp, self.fp_choice) for oid, mol in self.targets]
            if n < 64:
                raw = []
                for t in tasks:
                    if cancel_ev is not None and cancel_ev.is_set():
                        break
                    raw.append(_one(t))
            else:
                raw = []
                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    pending = {ex.submit(_one, t) for t in tasks}
                    while pending:
                        if cancel_ev is not None and cancel_ev.is_set():
                            for f in pending:
                                f.cancel()
                            for f in list(pending):
                                if f.done():
                                    try:
                                        raw.append(f.result())
                                    except Exception:
                                        pass
                                    pending.discard(f)
                            break
                        completed, pending = wait(pending, timeout=0.35, return_when=FIRST_COMPLETED)
                        for f in completed:
                            if f.cancelled():
                                continue
                            try:
                                raw.append(f.result())
                            except Exception:
                                pass
            rows = [x for x in raw if x is not None]
            rows.sort(key=lambda x: x[1], reverse=True)
            self.signals.finished.emit(rows)
        except Exception as e:
            logger.exception("FPSimilarityWorker failed")
            self.signals.failed.emit(str(e))

