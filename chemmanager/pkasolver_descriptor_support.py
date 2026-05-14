"""pkasolver-backed microstate prediction for descriptor columns (no Qt dependency in callables)."""

from __future__ import annotations

import logging
import math
import warnings

from rdkit import Chem
from rdkit.Chem import rdmolops

from chemmanager.workers import pka_predictor as pka_pred
from chemmanager.workers.pka_predictor import (
    _discard_stdio,
    _ensure_cairosvg_importable,
    _patch_pkasolver_dimorphite,
    _quieter_pkasolver_dependency_loggers,
    isolated_sys_argv_for_embedded_cli,
)
from chemmanager.workers.protomer_generator import estimate_protomer_populations_from_states

logger = logging.getLogger(__name__)

INT_FNS_NEED_PKASOLVER = frozenset({"LOGD74", "LOGS74", "CNS_MPO"})


def int_fns_need_pkasolver(int_fns) -> bool:
    return any(isinstance(f, str) and f in INT_FNS_NEED_PKASOLVER for f in int_fns)


def microstates_for_mol(mol: Chem.Mol) -> list | None:
    """Return pkasolver microstate list, or ``None`` if pkasolver is unavailable or prediction fails."""
    if mol is None:
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
            if pka_pred._query_model_singleton is None:
                pka_pred._query_model_singleton = QueryModel()
            qm = pka_pred._query_model_singleton
    except Exception:
        logger.warning("pkasolver QueryModel load failed for descriptors", exc_info=True)
        return None

    try:
        with _discard_stdio(), isolated_sys_argv_for_embedded_cli():
            return calculate_microstate_pka_values(mol, query_model=qm)
    except Exception:
        logger.debug("microstate pKa prediction failed for descriptor row", exc_info=True)
        return None


def most_basic_pka_from_states(states: list) -> float | None:
    """Highest microstate pKa (same convention as pKa tool “most basic only”)."""
    if not states:
        return None
    return max(float(s.pka) for s in states)


def neutral_fraction_from_states(states: list, ph: float = 7.4) -> float:
    """
    Mole fraction of net-neutral protomer states at ``ph`` (0–1).

    Uses the same HH-style protomer populations as :func:`estimate_protomer_populations_from_states`.
    """
    pops = estimate_protomer_populations_from_states(states, ph)
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
