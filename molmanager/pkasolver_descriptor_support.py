"""pkasolver-backed microstate prediction for descriptor columns (no Qt dependency in callables).

Uses **pkasolver** (Mayr et al., Front. Chem. 2022, doi:10.3389/fchem.2022.866585) with the same
**Dimorphite-DL** integration as ``pka_predictor`` (Ropp et al., J. Cheminform. 2019,
doi:10.1186/s13321-019-0336-9). Plain-text citations: ``molmanager.science_citations``.

Neutral fractions at a given pH come from ``estimate_protomer_populations_from_states`` in
``protomer_generator`` (independent-site HH over microstates; see that module and
``science_citations.LOGD_LOGS_ION``).
"""

from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass
from types import SimpleNamespace

from rdkit import Chem
from rdkit.Chem import rdmolops

from molmanager.workers import pka_predictor as pka_pred
from molmanager.workers.pka_predictor import (
    _discard_stdio,
    _ensure_cairosvg_importable,
    _patch_pkasolver_dimorphite,
    _quieter_pkasolver_dependency_loggers,
    isolated_sys_argv_for_embedded_cli,
    prepare_mol_for_pkasolver,
)
from molmanager.workers.protomer_generator import estimate_protomer_populations_from_states

logger = logging.getLogger(__name__)

INT_FNS_NEED_PKASOLVER = frozenset({"LOGD74", "LOGS74", "CNS_MPO", "AB_MPS"})


@dataclass(frozen=True)
class PicklableMicrostate:
    """Process-pool-safe snapshot of one pkasolver microstate (RDKit mols as binary blobs)."""

    pka: float
    protonated_mol: bytes | None
    deprotonated_mol: bytes | None
    ph7_mol: bytes | None


def _mol_to_binary(mol: Chem.Mol | None) -> bytes | None:
    if mol is None:
        return None
    try:
        return mol.ToBinary()
    except Exception:
        return None


def microstates_to_picklable(states: list) -> list[PicklableMicrostate]:
    """Convert pkasolver microstate objects to plain data safe for multiprocessing IPC."""
    out: list[PicklableMicrostate] = []
    for s in states:
        out.append(
            PicklableMicrostate(
                pka=float(s.pka),
                protonated_mol=_mol_to_binary(s.protonated_mol),
                deprotonated_mol=_mol_to_binary(s.deprotonated_mol),
                ph7_mol=_mol_to_binary(getattr(s, "ph7_mol", None)),
            )
        )
    return out


def _mol_from_binary(blob: bytes | None) -> Chem.Mol | None:
    if not blob:
        return None
    try:
        return Chem.Mol(blob)
    except Exception:
        return None


def hydrate_microstates(states: list) -> list:
    """Restore pkasolver-like microstate objects (``SimpleNamespace``) from picklable snapshots."""
    if not states:
        return []
    if isinstance(states[0], PicklableMicrostate):
        return [
            SimpleNamespace(
                pka=s.pka,
                protonated_mol=_mol_from_binary(s.protonated_mol),
                deprotonated_mol=_mol_from_binary(s.deprotonated_mol),
                ph7_mol=_mol_from_binary(s.ph7_mol),
            )
            for s in states
        ]
    return states


def int_fns_need_pkasolver(int_fns) -> bool:
    return any(isinstance(f, str) and f in INT_FNS_NEED_PKASOLVER for f in int_fns)


def microstates_for_mol(mol: Chem.Mol) -> list | None:
    """Return pkasolver microstate list, or ``None`` if pkasolver is unavailable or prediction fails."""
    safe = prepare_mol_for_pkasolver(mol)
    if safe is None:
        return None
    with _quieter_pkasolver_dependency_loggers():
        try:
            _ensure_cairosvg_importable()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                _patch_pkasolver_dimorphite()
                from pkasolver.query import QueryModel, calculate_microstate_pka_values
        except Exception:
            logger.debug("pkasolver not available for descriptor microstates", exc_info=True)
            return None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            with pka_pred._query_model_lock:
                if pka_pred._query_model_singleton is None:
                    pka_pred._query_model_singleton = QueryModel()
                qm = pka_pred._query_model_singleton
                with _discard_stdio(), isolated_sys_argv_for_embedded_cli():
                    states = calculate_microstate_pka_values(safe, query_model=qm)
                if not states:
                    return None
                return microstates_to_picklable(states)
    except Exception:
        logger.debug("microstate pKa prediction failed for descriptor row", exc_info=True)
        return None


def most_basic_pka_from_states(states: list) -> float | None:
    """Highest microstate pKa (same convention as pKa tool “most basic only”)."""
    states = hydrate_microstates(states)
    if not states:
        return None
    return max(float(s.pka) for s in states)


def neutral_fraction_from_states(states: list, ph: float = 7.4) -> float:
    """
    Mole fraction of net-neutral protomer states at ``ph`` (0–1).

    Uses the same HH-style protomer populations as :func:`estimate_protomer_populations_from_states`.
    """
    pops = estimate_protomer_populations_from_states(hydrate_microstates(states), ph)
    neutral = 0.0
    for _smi, pct, m in pops:
        if m is not None and rdmolops.GetFormalCharge(m) == 0:
            neutral += pct
    return max(neutral / 100.0, 1e-15)


def logd74_from_microstates(states: list, clogp: float) -> float:
    """
    Octanol-water log D at pH 7.4 from RDKit cLogP and pkasolver microstate populations.

    log D = log P + log10(f_neutral) for the usual partition model.
    """
    return clogp + math.log10(neutral_fraction_from_states(states, 7.4))


def logs74_from_microstates(states: list, log_s_intrinsic: float) -> float:
    """
    Approximate aqueous log10(S / mol L⁻¹) at pH 7.4 from intrinsic log S and ionization.

    Uses log S_aq ≈ log S_intrinsic − log10(f_neutral) so ionized fractions (more soluble)
    increase total solubility vs the neutral intrinsic baseline (ESOL).
    """
    return log_s_intrinsic - math.log10(neutral_fraction_from_states(states, 7.4))
