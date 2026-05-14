"""Medicinal-chemistry helpers used by descriptor jobs (Lipinski, CNS MPO-style scores).

The CNS MPO piecewise desirability curves follow Wager et al., ACS Chem. Neurosci. 2010, Table 1
(PMC3368654). When pkasolver microstates are available (see ``pkasolver_descriptor_support``),
cLogD7.4 and the MPO pKa term use those predictions; otherwise cLogD and pKa fall back to simple
RDKit-based heuristics. Log D7.4 as a table column always uses pkasolver when available.
"""

from __future__ import annotations

import math

from rdkit import Chem
from rdkit.Chem import Descriptors, Lipinski, inchi, rdMolDescriptors

from chemmanager.pkasolver_descriptor_support import (
    logd74_from_microstates,
    microstates_for_mol,
    most_basic_pka_from_states,
)


def _mono_dec(x: float, x_good: float, x_bad: float) -> float:
    """Monotonic decreasing desirability: 1 for x <= x_good, 0 for x >= x_bad, linear between."""
    if x <= x_good:
        return 1.0
    if x >= x_bad:
        return 0.0
    return 1.0 - (x - x_good) / (x_bad - x_good)


def _tpsa_hump(tpsa: float) -> float:
    """TPSA hump from Wager 2010 Table 1 (piecewise linear)."""
    if tpsa <= 20.0:
        return 0.0
    if tpsa <= 40.0:
        return (tpsa - 20.0) / (40.0 - 20.0)
    if tpsa <= 90.0:
        return 1.0
    if tpsa <= 120.0:
        return 1.0 - (tpsa - 90.0) / (120.0 - 90.0)
    return 0.0


def lipinski_violations(mol: Chem.Mol) -> int:
    """Count Lipinski Rule-of-5 violations (0–4) using RDKit Lipinski H-bond definitions."""
    n = 0
    if Descriptors.MolWt(mol) > 500.0:
        n += 1
    if Descriptors.MolLogP(mol) > 5.0:
        n += 1
    if Lipinski.NumHDonors(mol) > 5:
        n += 1
    if Lipinski.NumHAcceptors(mol) > 10:
        n += 1
    return n


def ro5_pass(mol: Chem.Mol) -> str:
    """Human-readable Ro5 compliance for table cells."""
    return "Yes" if lipinski_violations(mol) == 0 else "No"


def _approx_pka_most_basic(mol: Chem.Mol) -> float:
    """Rough conjugate-acid pKa proxy when pkasolver microstates are unavailable."""
    n_n = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 7)
    if n_n == 0:
        return 2.0
    return min(10.5, 7.6 + 0.35 * float(max(0, n_n - 1)))


def _clogd7_heuristic(clogp: float, pka_bh_plus: float) -> float:
    """Fallback cLogD7.4 from cLogP and a single pKa (monoprotic-base-style correction)."""
    if pka_bh_plus <= 7.4:
        return clogp
    return clogp - math.log10(1.0 + 10.0 ** (pka_bh_plus - 7.4))


def logd74_value(mol: Chem.Mol, states: list | None = None) -> float:
    """
    Log D7.4 from RDKit cLogP and pkasolver microstates (required).

    Raises ``ValueError`` if microstates are missing so the descriptor layer can show N/A.
    """
    st = states if states is not None else microstates_for_mol(mol)
    if not st:
        raise ValueError("pkasolver microstates unavailable")
    return logd74_from_microstates(st, float(Descriptors.MolLogP(mol)))


def cns_mpo_score(mol: Chem.Mol, states: list | None = None) -> float:
    """
    Composite CNS MPO-style score (0–6) from Wager 2010 Table 1 desirability functions.

    If ``states`` is omitted, pkasolver is attempted once per call; pass precomputed microstates
    from a shared row context when computing multiple pkasolver-backed columns for one molecule.
    """
    clogp = float(Descriptors.MolLogP(mol))
    st = states if states is not None else microstates_for_mol(mol)
    if st:
        pka_mb = most_basic_pka_from_states(st)
        clogd = logd74_from_microstates(st, clogp)
    else:
        pka_mb = _approx_pka_most_basic(mol)
        clogd = _clogd7_heuristic(clogp, pka_mb)

    mw = float(Descriptors.MolWt(mol))
    tpsa = float(Descriptors.TPSA(mol))
    hbd = float(Lipinski.NumHDonors(mol))

    t0_p = _mono_dec(clogp, 3.0, 5.0)
    t0_d = _mono_dec(clogd, 2.0, 4.0)
    t0_mw = _mono_dec(mw, 360.0, 500.0)
    t0_tpsa = _tpsa_hump(tpsa)
    t0_hbd = _mono_dec(hbd, 0.5, 3.5)
    t0_pka = _mono_dec(pka_mb, 8.0, 10.0)
    return t0_p + t0_d + t0_mw + t0_tpsa + t0_hbd + t0_pka


def mol_inchi_key(mol: Chem.Mol) -> str:
    """Standard InChI Key or empty string if RDKit cannot generate one."""
    try:
        key = inchi.MolToInchiKey(mol)
    except Exception:
        return ""
    return key or ""


def mol_formula(mol: Chem.Mol) -> str:
    """Hill-system molecular formula."""
    return rdMolDescriptors.CalcMolFormula(mol)
