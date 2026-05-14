"""Medicinal-chemistry helpers used by descriptor jobs (Lipinski, CNS MPO-style scores).

The CNS MPO piecewise desirability curves follow Wager et al., ACS Chem. Neurosci. 2010, Table 1
(PMC3368654). ChemManager does not ship ChemAxon cLogD7.4 or experimental pKa; cLogD7.4 is
estimated from cLogP and a rough conjugate-acid pKa heuristic for ionization at pH 7.4, and the
same heuristic feeds the pKa desirability term. Use dedicated pKa tools for high-stakes decisions.
"""

from __future__ import annotations

import math

from rdkit import Chem
from rdkit.Chem import Descriptors, Lipinski, inchi, rdMolDescriptors


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
    """Rough conjugate-acid pKa proxy for the most basic site (triage only; no pkasolver)."""
    n_n = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 7)
    if n_n == 0:
        return 2.0
    return min(10.5, 7.6 + 0.35 * float(max(0, n_n - 1)))


def _clogd7_estimate(clogp: float, pka_bh_plus: float) -> float:
    """Estimate log D7.4 from cLogP and fraction ionized (monoprotic-base-style correction)."""
    if pka_bh_plus <= 7.4:
        return clogp
    return clogp - math.log10(1.0 + 10.0 ** (pka_bh_plus - 7.4))


def cns_mpo_score(mol: Chem.Mol) -> float:
    """Composite CNS MPO-style score (0–6) from Wager 2010 Table 1 desirability functions."""
    clogp = float(Descriptors.MolLogP(mol))
    pka = _approx_pka_most_basic(mol)
    clogd = _clogd7_estimate(clogp, pka)
    mw = float(Descriptors.MolWt(mol))
    tpsa = float(Descriptors.TPSA(mol))
    hbd = float(Lipinski.NumHDonors(mol))

    t0_p = _mono_dec(clogp, 3.0, 5.0)
    t0_d = _mono_dec(clogd, 2.0, 4.0)
    t0_mw = _mono_dec(mw, 360.0, 500.0)
    t0_tpsa = _tpsa_hump(tpsa)
    t0_hbd = _mono_dec(hbd, 0.5, 3.5)
    t0_pka = _mono_dec(pka, 8.0, 10.0)
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
