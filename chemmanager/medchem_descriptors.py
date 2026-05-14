"""Medicinal-chemistry helpers used by descriptor jobs (Lipinski, CNS MPO-style scores, solubility).

Canonical citations (plain text, copy-paste friendly) live in ``chemmanager.science_citations``.

* **CNS MPO** — Wager et al., ACS Chem. Neurosci. 2010 (doi:10.1021/cn100008c); Table 1 / PMC3368654.
* **ESOL intrinsic log S** — Delaney, J. Chem. Inf. Comput. Sci. 2004 (doi:10.1021/ci034243r).
* **pkasolver microstates** — Mayr et al., Front. Chem. 2022 (doi:10.3389/fchem.2022.866585); GitHub mayrf/pkasolver.
* **Dimorphite-DL** (inside pkasolver) — Ropp et al., J. Cheminform. 2019 (doi:10.1186/s13321-019-0336-9).

When pkasolver microstates are available (``pkasolver_descriptor_support``), LogD 7.4, LogS 7.4, and the
CNS MPO cLogD / pKa legs use those predictions plus RDKit ``Crippen.MolLogP``; otherwise cLogD / pKa fall back
to simple heuristics. LogD 7.4 and LogS 7.4 columns require pkasolver. Neutral fractions at pH 7.4 reuse the
same Henderson–Hasselbalch protomer pooling as ``estimate_protomer_populations_from_states`` (independent
sites; approximate).

**LogS intrinsic** uses the original Delaney ESOL equation (log10 mol L⁻¹). **LogS 7.4** augments that with
−log10(f_neutral) from pkasolver populations (same f_neutral as LogD 7.4).
"""

from __future__ import annotations

import math

from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, Lipinski, inchi, rdMolDescriptors

from chemmanager.pkasolver_descriptor_support import (
    logd74_from_microstates,
    logs74_from_microstates,
    microstates_for_mol,
    most_basic_pka_from_states,
)

_AROM_ATOM_QUERY = Chem.MolFromSmarts("a")


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


def _aromatic_proportion(mol: Chem.Mol) -> float:
    """Aromatic heavy atoms / total heavy atoms (Delaney ESOL aromatic proportion)."""
    n = mol.GetNumAtoms()
    if n <= 0 or _AROM_ATOM_QUERY is None:
        return 0.0
    return len(mol.GetSubstructMatches(_AROM_ATOM_QUERY)) / float(n)


def esol_logS_intrinsic(mol: Chem.Mol) -> float:
    """
    ESOL intrinsic log10(S / mol L⁻¹); Delaney J. Chem. Inf. Comput. Sci. 2004 (original coefficients).

    Neutral/intrinsic aqueous solubility estimate for screening, not a measured S0.
    """
    mw = float(Descriptors.MolWt(mol))
    logp = float(Crippen.MolLogP(mol))
    rotors = float(Lipinski.NumRotatableBonds(mol))
    ap = _aromatic_proportion(mol)
    return 0.16 - 0.63 * logp - 0.0062 * mw + 0.066 * rotors - 0.74 * ap


def logd74_value(mol: Chem.Mol, states: list | None = None) -> float:
    """
    LogD 7.4 from RDKit cLogP and pkasolver microstates (required).

    Raises ``ValueError`` if microstates are missing so the descriptor layer can show N/A.
    """
    st = states if states is not None else microstates_for_mol(mol)
    if not st:
        raise ValueError("pkasolver microstates unavailable")
    return logd74_from_microstates(st, float(Crippen.MolLogP(mol)))


def logs74_value(mol: Chem.Mol, states: list | None = None) -> float:
    """
    Approximate aqueous log10(S / mol L⁻¹) at pH 7.4 from ESOL intrinsic log S and pkasolver states.

    Raises ``ValueError`` if microstates are missing so the descriptor layer can show N/A.
    """
    st = states if states is not None else microstates_for_mol(mol)
    if not st:
        raise ValueError("pkasolver microstates unavailable")
    return logs74_from_microstates(st, esol_logS_intrinsic(mol))


def cns_mpo_score(mol: Chem.Mol, states: list | None = None) -> float:
    """
    Composite CNS MPO-style score (0–6) from Wager 2010 Table 1 desirability functions.

    If ``states`` is omitted, pkasolver is attempted once per call; pass precomputed microstates
    from a shared row context when computing multiple pkasolver-backed columns for one molecule.
    """
    clogp = float(Crippen.MolLogP(mol))
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
