# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

"""Matched molecular pair (MMP) analysis via RDKit ``rdMMPA.FragmentMol``.

Implements the Hussain / Rea single- (and optional multi-) cut indexing scheme:
molecules that share the same constant core but differ in the variable
sidechain(s) form matched molecular pairs. Activity differences are computed
when numeric activity values are supplied.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from rdkit import Chem
from rdkit.Chem import rdFMCS, rdMMPA

# Core key type: (core_smiles, sidechain_tuple)
CoreSideKey = tuple[str, tuple[str, ...]]


@dataclass(frozen=True)
class MmpPair:
    """One matched molecular pair linking two table molecules."""

    oid_a: int
    oid_b: int
    smiles_a: str
    smiles_b: str
    activity_a: float
    activity_b: float
    delta_activity: float
    transform: str
    core: str
    sidechain_a: str
    sidechain_b: str


def _heavy_atom_count(smiles: str, cache: dict[str, int] | None = None) -> int:
    if cache is not None and smiles in cache:
        return cache[smiles]
    mol = Chem.MolFromSmiles(smiles)
    n = 0 if mol is None else int(mol.GetNumHeavyAtoms())
    if cache is not None:
        cache[smiles] = n
    return n


def _normalize_fragment_smiles(smiles: str, cache: dict[str, str] | None = None) -> str:
    if cache is not None and smiles in cache:
        return cache[smiles]
    mol = Chem.MolFromSmiles(smiles)
    out = (smiles or "").strip() if mol is None else Chem.MolToSmiles(mol, isomericSmiles=True)
    if cache is not None:
        cache[smiles] = out
    return out


def fragment_keys_for_mol(
    mol: Chem.Mol,
    *,
    max_cuts: int = 1,
    max_cut_bonds: int = 20,
) -> list[CoreSideKey]:
    """Return unique ``(core, sidechains)`` keys for *mol* (list form for pickling)."""
    return list(iter_core_sidechain_keys(mol, max_cuts=max_cuts, max_cut_bonds=max_cut_bonds))


def iter_core_sidechain_keys(
    mol: Chem.Mol,
    *,
    max_cuts: int = 1,
    max_cut_bonds: int = 20,
) -> Iterable[CoreSideKey]:
    """
    Yield ``(core_smiles, sidechain_smiles_tuple)`` keys for *mol*.

    For single cuts RDKit returns an empty core and ``side.core`` in the second
    field; the larger fragment is treated as the constant core. Equal-sized
    fragments yield both orientations.
    """
    if mol is None:
        return
    max_cuts = max(1, min(int(max_cuts), 3))
    max_cut_bonds = max(1, int(max_cut_bonds))
    try:
        frags = rdMMPA.FragmentMol(
            mol,
            maxCuts=max_cuts,
            maxCutBonds=max_cut_bonds,
            resultsAsMols=False,
        )
    except Exception:
        return

    norm_cache: dict[str, str] = {}
    heavy_cache: dict[str, int] = {}
    seen: set[CoreSideKey] = set()
    for core_smi, rest in frags or ():
        core_smi = (core_smi or "").strip()
        rest = (rest or "").strip()
        if not rest:
            continue
        if core_smi:
            sides = tuple(
                sorted(
                    _normalize_fragment_smiles(p, norm_cache)
                    for p in rest.split(".")
                    if p.strip()
                )
            )
            if not sides:
                continue
            key = (_normalize_fragment_smiles(core_smi, norm_cache), sides)
            if key not in seen:
                seen.add(key)
                yield key
            continue

        parts = [p for p in rest.split(".") if p.strip()]
        if len(parts) != 2:
            continue
        a = _normalize_fragment_smiles(parts[0], norm_cache)
        b = _normalize_fragment_smiles(parts[1], norm_cache)
        na, nb = _heavy_atom_count(a, heavy_cache), _heavy_atom_count(b, heavy_cache)
        if na == nb:
            orientations = [(a, (b,)), (b, (a,))]
        elif na > nb:
            orientations = [(a, (b,))]
        else:
            orientations = [(b, (a,))]
        for key in orientations:
            if key not in seen:
                seen.add(key)
                yield key


def pairs_from_fragment_records(
    records: list[tuple[int, str, float, list[CoreSideKey]]],
    *,
    max_variable_heavy_atoms: int | None = 13,
    min_activity_difference: float = 0.0,
    cancel_check: Callable[[], bool] | None = None,
) -> list[MmpPair]:
    """
    Build MMP pairs from precomputed fragment keys.

    Each record is ``(oid, smiles, activity, fragment_keys)``.
    """
    min_dact = max(0.0, float(min_activity_difference))
    heavy_cache: dict[str, int] = {}
    index: dict[tuple[str, int], list[tuple[int, str, float, str]]] = defaultdict(list)

    for i, (oid, smiles, activity, keys) in enumerate(records):
        if cancel_check is not None and i % 32 == 0 and cancel_check():
            return []
        if not smiles or not keys:
            continue
        for core, sides in keys:
            n_cuts = len(sides)
            if max_variable_heavy_atoms is not None:
                var_atoms = sum(_heavy_atom_count(s, heavy_cache) for s in sides)
                if var_atoms > int(max_variable_heavy_atoms):
                    continue
            side_key = ".".join(sides)
            index[(core, n_cuts)].append((int(oid), side_key, float(activity), smiles))

    # One entry per unordered molecule pair; prefer the smallest structural change.
    best_by_mols: dict[tuple[int, int], MmpPair] = {}
    heavy_pref_cache: dict[str, int] = {}

    for ci, ((core, _n_cuts), entries) in enumerate(index.items()):
        if cancel_check is not None and ci % 16 == 0 and cancel_check():
            break
        by_side: dict[str, list[tuple[int, float, str]]] = defaultdict(list)
        for oid, side_key, activity, smiles in entries:
            by_side[side_key].append((oid, activity, smiles))
        side_keys = list(by_side.keys())
        for i in range(len(side_keys)):
            for j in range(i + 1, len(side_keys)):
                sa, sb = side_keys[i], side_keys[j]
                for oid_a, act_a, smi_a in by_side[sa]:
                    for oid_b, act_b, smi_b in by_side[sb]:
                        if oid_a == oid_b:
                            continue
                        left_oid, right_oid = oid_a, oid_b
                        left_act, right_act = act_a, act_b
                        left_smi, right_smi = smi_a, smi_b
                        left_side, right_side = sa, sb
                        if left_oid > right_oid:
                            left_oid, right_oid = right_oid, left_oid
                            left_act, right_act = right_act, left_act
                            left_smi, right_smi = right_smi, left_smi
                            left_side, right_side = right_side, left_side
                        delta = right_act - left_act
                        if abs(delta) < min_dact:
                            continue
                        transform = f"{left_side}>>{right_side}"
                        cand = MmpPair(
                            oid_a=left_oid,
                            oid_b=right_oid,
                            smiles_a=left_smi,
                            smiles_b=right_smi,
                            activity_a=left_act,
                            activity_b=right_act,
                            delta_activity=delta,
                            transform=transform,
                            core=core,
                            sidechain_a=left_side,
                            sidechain_b=right_side,
                        )
                        mol_key = (left_oid, right_oid)
                        prev = best_by_mols.get(mol_key)
                        if prev is None or _pair_rank(cand, heavy_pref_cache) < _pair_rank(
                            prev, heavy_pref_cache
                        ):
                            best_by_mols[mol_key] = cand

    pairs = list(best_by_mols.values())
    pairs.sort(key=lambda p: (-abs(p.delta_activity), p.transform, p.oid_a, p.oid_b))
    return pairs


def _pair_rank(pair: MmpPair, heavy_cache: dict[str, int]) -> tuple[int, int, str]:
    """Lower is better: fewer changing atoms, then shorter transform SMILES."""
    var_atoms = _heavy_atom_count(pair.sidechain_a, heavy_cache) + _heavy_atom_count(
        pair.sidechain_b, heavy_cache
    )
    return (var_atoms, len(pair.transform), pair.transform)


def find_matched_molecular_pairs(
    records: list[tuple[int, Chem.Mol, float]],
    *,
    max_cuts: int = 1,
    max_cut_bonds: int = 20,
    max_variable_heavy_atoms: int | None = 13,
    min_activity_difference: float = 0.0,
    cancel_check: Callable[[], bool] | None = None,
) -> list[MmpPair]:
    """
    Find matched molecular pairs among *records* ``(oid, mol, activity)``.

    ``max_variable_heavy_atoms`` limits the size of the changing fragment
    (``None`` disables the limit). ``min_activity_difference`` keeps only pairs
    whose absolute Δactivity is at least that threshold (0 disables the filter).
    Pairs are sorted by absolute Δactivity descending, then by transform.
    """
    frag_records: list[tuple[int, str, float, list[CoreSideKey]]] = []
    for i, (oid, mol, activity) in enumerate(records):
        if cancel_check is not None and i % 16 == 0 and cancel_check():
            return []
        if mol is None:
            continue
        try:
            smiles = Chem.MolToSmiles(mol, isomericSmiles=True)
        except Exception:
            continue
        if not smiles:
            continue
        keys = fragment_keys_for_mol(mol, max_cuts=max_cuts, max_cut_bonds=max_cut_bonds)
        frag_records.append((int(oid), smiles, float(activity), keys))
    return pairs_from_fragment_records(
        frag_records,
        max_variable_heavy_atoms=max_variable_heavy_atoms,
        min_activity_difference=min_activity_difference,
        cancel_check=cancel_check,
    )


def assemble_mmp_table_annotations(
    pairs: list[MmpPair],
    *,
    activity_column: str,
) -> tuple[list[tuple[int, dict[str, str]]], list[str]]:
    """
    Build per-molecule annotation columns for write-back to the main table.

    Columns: ``MMP_Partners``, ``MMP_Transforms``, ``MMP_Delta_<activity>``.
    Multiple pairs for one OID are joined with ``; `` (sorted by |Δ| desc).
    """
    delta_header = f"MMP_Delta_{activity_column}" if activity_column else "MMP_Delta"
    headers = ["MMP_Partners", "MMP_Transforms", delta_header]
    by_oid: dict[int, list[tuple[float, str, str, str]]] = defaultdict(list)

    for p in pairs:
        # From A's perspective: A -> B
        by_oid[p.oid_a].append(
            (abs(p.delta_activity), str(p.oid_b), p.transform, _fmt_delta(p.delta_activity))
        )
        # From B's perspective: reverse transform and sign
        rev = f"{p.sidechain_b}>>{p.sidechain_a}"
        by_oid[p.oid_b].append(
            (abs(p.delta_activity), str(p.oid_a), rev, _fmt_delta(-p.delta_activity))
        )

    rows: list[tuple[int, dict[str, str]]] = []
    for oid, items in by_oid.items():
        items.sort(key=lambda t: (-t[0], t[1], t[2]))
        partners = "; ".join(t[1] for t in items)
        transforms = "; ".join(t[2] for t in items)
        deltas = "; ".join(t[3] for t in items)
        rows.append(
            (
                oid,
                {
                    "MMP_Partners": partners,
                    "MMP_Transforms": transforms,
                    delta_header: deltas,
                },
            )
        )
    rows.sort(key=lambda r: r[0])
    return rows, headers


def _fmt_delta(value: float) -> str:
    text = f"{value:.4g}"
    if text.startswith("-") or text == "0":
        return text
    return f"+{text}"


def highlight_atoms_for_pair(
    mol_a: Chem.Mol,
    mol_b: Chem.Mol,
) -> tuple[list[int], list[int]]:
    """
    Return atom indices to highlight on each molecule (variable / non-MCS atoms).

    Falls back to empty lists when MCS cannot be found.
    """
    if mol_a is None or mol_b is None:
        return [], []
    try:
        res = rdFMCS.FindMCS(
            [mol_a, mol_b],
            timeout=1,
            matchValences=True,
            ringMatchesRingOnly=True,
            completeRingsOnly=False,
        )
    except Exception:
        return [], []
    if res is None or res.canceled or res.numAtoms < 1 or not res.smartsString:
        return [], []
    try:
        query = Chem.MolFromSmarts(res.smartsString)
    except Exception:
        return [], []
    if query is None:
        return [], []

    def _variable_atoms(mol: Chem.Mol) -> list[int]:
        matches = mol.GetSubstructMatches(query)
        if not matches:
            return []
        common = set(matches[0])
        return [int(a.GetIdx()) for a in mol.GetAtoms() if int(a.GetIdx()) not in common]

    return _variable_atoms(mol_a), _variable_atoms(mol_b)
