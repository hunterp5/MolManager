"""Generate protomer sets from pkasolver microstate pKas (approximate populations at a target pH).

Microstate pKas: **pkasolver** (Mayr et al., Front. Chem. 2022, doi:10.3389/fchem.2022.866585;
https://github.com/mayrf/pkasolver). Enumeration path matches the pKa predictor (Dimorphite-DL;
Ropp et al., J. Cheminform. 2019, doi:10.1186/s13321-019-0336-9).

Population math: independent-site Henderson–Hasselbalch pooling over those microstates (same
neutral-fraction idea as LogD 7.4 / LogS 7.4 descriptors; approximate). See ``science_citations``.
"""

from __future__ import annotations

import logging
import threading
import warnings
from collections import defaultdict

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal
from rdkit import Chem

from ..utils import mol_to_canonical_smiles
from .pka_predictor import (
    _discard_stdio,
    _ensure_cairosvg_importable,
    _patch_pkasolver_dimorphite,
    _quieter_pkasolver_dependency_loggers,
    _safe_emit,
    isolated_sys_argv_for_embedded_cli,
)

logger = logging.getLogger(__name__)


def estimate_protomer_populations_from_states(states, pH: float) -> list[tuple[str, float, Chem.Mol]]:
    """
    Approximate protomer mole fractions at ``pH`` from pkasolver microstates.

    Each microstate is treated as an independent Henderson–Hasselbalch equilibrium between
    ``protonated_mol`` and ``deprotonated_mol``; contributions are summed per canonical SMILES
    and renormalized. This ignores coupling between sites and is only a rough guide.
    """
    if not states:
        return []
    key_to_mol: dict[str, Chem.Mol] = {}
    acc: defaultdict[str, float] = defaultdict(float)
    for s in states:
        pka = float(s.pka)
        pm = s.protonated_mol
        dm = s.deprotonated_mol
        if pm is None or dm is None:
            continue
        sp = mol_to_canonical_smiles(pm)
        sd = mol_to_canonical_smiles(dm)
        if not sp or not sd:
            continue
        if sp not in key_to_mol:
            key_to_mol[sp] = Chem.Mol(pm)
        if sd not in key_to_mol:
            key_to_mol[sd] = Chem.Mol(dm)
        # Acid dissociation HA ⇌ H⁺ + A⁻ with macro/micro pKa: fraction A⁻ = 1 / (1 + 10^(pKa − pH))
        frac_deprot = 1.0 / (1.0 + 10.0 ** (pka - pH))
        frac_prot = 1.0 - frac_deprot
        acc[sp] += frac_prot
        acc[sd] += frac_deprot
    total = sum(acc.values())
    if total <= 0:
        ref = states[0].ph7_mol
        if ref is None:
            return []
        smi = mol_to_canonical_smiles(ref)
        if not smi:
            return []
        return [(smi, 100.0, Chem.Mol(ref))]
    out: list[tuple[str, float, Chem.Mol]] = []
    for k, v in acc.items():
        mol = key_to_mol.get(k)
        if mol is None:
            continue
        out.append((k, 100.0 * v / total, mol))
    out.sort(key=lambda t: -t[1])
    return out


class ProtomerGeneratorSignals(QObject):
    finished = pyqtSignal(list)  # list[tuple[int | None, str, float]]  source_oid, smiles, pct
    failed = pyqtSignal(str)


class ProtomerGeneratorWorker(QRunnable):
    """Enumerate protomers per input molecule and estimate populations at a target pH."""

    def __init__(
        self,
        rows: list[tuple[int | None, Chem.Mol | None]],
        pH: float,
        worker_signals,
        protomer_signals: ProtomerGeneratorSignals,
        cancel_event: threading.Event | None = None,
    ):
        super().__init__()
        self.rows = rows
        self.pH = float(pH)
        self.worker_signals = worker_signals
        self.protomer_signals = protomer_signals
        self.cancel_event = cancel_event

    def run(self) -> None:
        from . import pka_predictor as pka_mod

        with _quieter_pkasolver_dependency_loggers():
            try:
                _ensure_cairosvg_importable()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    _patch_pkasolver_dimorphite()
                    from pkasolver.query import QueryModel, calculate_microstate_pka_values
            except Exception as e:
                logger.exception("Protomer generator: failed to import pkasolver stack")
                _safe_emit(
                    self.protomer_signals,
                    "failed",
                    "Could not load pkasolver (missing PyTorch / torch-geometric / pkasolver?). "
                    f"Details: {e}",
                )
                return

            tot = max(len(self.rows), 1)
            cancel_ev = self.cancel_event
            combined: list[tuple[int | None, str, float]] = []

            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    if pka_mod._query_model_singleton is None:
                        pka_mod._query_model_singleton = QueryModel()
                    qm = pka_mod._query_model_singleton
            except Exception as e:
                logger.exception("Protomer generator: model load failed")
                _safe_emit(self.protomer_signals, "failed", f"Could not load pkasolver neural models: {e}")
                return

            try:
                self.worker_signals.tool_progress.emit("Generate protomers…", 0, tot)
            except Exception:
                pass

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                for done, (oid, mol) in enumerate(self.rows, start=1):
                    if cancel_ev is not None and cancel_ev.is_set():
                        break
                    try:
                        self.worker_signals.tool_progress.emit("Generate protomers…", done, tot)
                    except Exception:
                        pass
                    if mol is None:
                        continue
                    try:
                        with _discard_stdio(), isolated_sys_argv_for_embedded_cli():
                            states = calculate_microstate_pka_values(mol, query_model=qm)
                        pops = estimate_protomer_populations_from_states(states, self.pH)
                        for smi, pct, _m in pops:
                            combined.append((oid, smi, pct))
                    except Exception as e:
                        logger.warning("Protomer generation failed for row %s: %s", oid, e)

            try:
                self.worker_signals.tool_progress.emit("Generate protomers…", tot, tot)
            except Exception:
                pass
            _safe_emit(self.protomer_signals, "finished", combined)
