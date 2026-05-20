"""Fingerprint similarity batch worker."""

from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from PyQt5.QtCore import QRunnable
from rdkit import Chem
from rdkit import DataStructs
from rdkit.Chem import AllChem, rdMolDescriptors, rdmolops

from ..utils import mol_to_canonical_smiles
from .signals import FPSimilaritySignals

logger = logging.getLogger(__name__)

# Labels for Tools → Fingerprint Similarity and Tools → Cluster (same strings → same fingerprints).
SIMILARITY_FP_TYPE_LABELS: list[str] = [
    "Morgan (r=2, n=1024)",
    "Morgan (r=3, n=1024)",
    "Morgan (r=2, n=2048)",
    "RDK (2048)",
    "MACCS (166)",
    "Atom pair (hashed, 2048 bits)",
    "Topological torsion (hashed, 2048 bits)",
]


def _rdk_fingerprint_bitvect(mol: Chem.Mol, max_path: int = 5, fp_size: int = 2048):
    """RDKit topological path fingerprint (ExplicitBitVect), compatible across RDKit versions."""
    getter = getattr(AllChem, "GetRDKFingerprint", None)
    if getter is not None:
        return getter(mol, maxPath=max_path, fpSize=fp_size)
    return rdmolops.RDKFingerprint(mol, minPath=1, maxPath=max_path, fpSize=fp_size)


def _morgan_radius_and_nbits(fp_choice: str) -> tuple[int, int]:
    """Parse ``r=`` / ``n=`` from UI labels; defaults match the original Morgan (r=2, n=1024)."""
    r = 2
    n = 1024
    m_r = re.search(r"r\s*=\s*(\d+)", fp_choice, re.I)
    m_n = re.search(r"n\s*=\s*(\d+)", fp_choice, re.I)
    if m_r:
        r = int(m_r.group(1))
    if m_n:
        n = int(m_n.group(1))
    return r, n


def _rdk_nbits_from_label(fp_choice: str) -> int:
    m = re.search(r"\((\d+)\)", fp_choice)
    if m:
        return max(64, min(8192, int(m.group(1))))
    return 2048


def _hashed_nbits_from_label(fp_choice: str, default: int = 2048) -> int:
    m = re.search(r"(\d+)\s*bits?", fp_choice, re.I)
    if m:
        return max(64, min(8192, int(m.group(1))))
    return default


def fingerprint_bitvect_for_ui_choice(mol: Chem.Mol, fp_choice: str):
    """Bit vector for fingerprint labels used in Fingerprint Similarity and Cluster dialogs."""
    try:
        if fp_choice.startswith("Morgan"):
            radius, n_bits = _morgan_radius_and_nbits(fp_choice)
            return AllChem.GetMorganFingerprintAsBitVect(mol, radius, nBits=n_bits)
        if fp_choice.startswith("RDK"):
            n = _rdk_nbits_from_label(fp_choice)
            return _rdk_fingerprint_bitvect(mol, max_path=5, fp_size=n)
        if fp_choice.startswith("MACCS"):
            return rdMolDescriptors.GetMACCSKeysFingerprint(mol)
        if fp_choice.startswith("Atom pair"):
            n_bits = _hashed_nbits_from_label(fp_choice, 2048)
            return AllChem.GetHashedAtomPairFingerprintAsBitVect(mol, nBits=n_bits)
        if fp_choice.startswith("Topological"):
            n_bits = _hashed_nbits_from_label(fp_choice, 2048)
            return AllChem.GetHashedTopologicalTorsionFingerprintAsBitVect(mol, nBits=n_bits)
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
                        completed, pending = wait(pending, timeout=0.08, return_when=FIRST_COMPLETED)
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
