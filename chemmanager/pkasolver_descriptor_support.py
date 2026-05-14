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

INT_FNS_NEED_PKASOLVER = frozenset({"LOGD74", "CNS_MPO"})


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


def logd74_from_microstates(states: list, clogp: float) -> float:
    """
    Octanol-water log D at pH 7.4 from RDKit cLogP and pkasolver microstate populations.

    Neutral fraction is the sum of protomer-state mole percents with net formal charge zero
    (same HH-style model as :func:`estimate_protomer_populations_from_states`).
    """
    pops = estimate_protomer_populations_from_states(states, 7.4)
    neutral = 0.0
    for _smi, pct, m in pops:
        if m is not None and rdmolops.GetFormalCharge(m) == 0:
            neutral += pct
    neutral_frac = max(neutral / 100.0, 1e-15)
    return clogp + math.log10(neutral_frac)
