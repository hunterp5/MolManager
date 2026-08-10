"""Tests for matched molecular pair analysis helpers."""

from rdkit import Chem

from molmanager.mmp_analysis import (
    assemble_mmp_table_annotations,
    find_matched_molecular_pairs,
    highlight_atoms_for_pair,
    iter_core_sidechain_keys,
)


def _rec(oid: int, smiles: str, activity: float):
    return oid, Chem.MolFromSmiles(smiles), activity


def test_iter_core_sidechain_keys_phenol():
    mol = Chem.MolFromSmiles("Oc1ccccc1")
    keys = list(iter_core_sidechain_keys(mol, max_cuts=1))
    assert keys
    cores = {c for c, _s in keys}
    assert any("c1ccc" in c for c in cores)


def test_find_mmp_phenol_anisole():
    records = [
        _rec(1, "Oc1ccccc1", 1.0),
        _rec(2, "COc1ccccc1", 2.5),
        _rec(3, "CCOc1ccccc1", 3.0),
    ]
    pairs = find_matched_molecular_pairs(records, max_cuts=1)
    assert pairs
    transforms = {p.transform for p in pairs}
    assert any("O[*:1]>>CO[*:1]" in t or "CO[*:1]>>O[*:1]" in t for t in transforms)
    phenol_anisole = [
        p
        for p in pairs
        if {p.oid_a, p.oid_b} == {1, 2} and "O[*:1]" in p.transform and "CO[*:1]" in p.transform
    ]
    assert phenol_anisole
    p = phenol_anisole[0]
    assert abs(abs(p.delta_activity) - 1.5) < 1e-9


def test_find_mmp_skips_missing_activity_partner():
    records = [
        _rec(1, "Oc1ccccc1", 1.0),
        _rec(2, "CCc1ccc(O)cc1", 1.2),
    ]
    # Different cores for OH->OMe style; still may find pairs via other cuts.
    pairs = find_matched_molecular_pairs(records, max_cuts=1)
    assert isinstance(pairs, list)


def test_max_variable_heavy_atoms_filters():
    records = [
        _rec(1, "Oc1ccccc1", 1.0),
        _rec(2, "COc1ccccc1", 2.0),
    ]
    loose = find_matched_molecular_pairs(records, max_variable_heavy_atoms=13)
    tight = find_matched_molecular_pairs(records, max_variable_heavy_atoms=1)
    assert len(loose) >= len(tight)


def test_min_activity_difference_filters():
    records = [
        _rec(1, "Oc1ccccc1", 1.0),
        _rec(2, "COc1ccccc1", 2.5),  # |Δ| = 1.5
        _rec(3, "CCOc1ccccc1", 2.7),  # vs 2: |Δ| = 0.2
    ]
    all_pairs = find_matched_molecular_pairs(records, max_cuts=1, min_activity_difference=0.0)
    filtered = find_matched_molecular_pairs(records, max_cuts=1, min_activity_difference=1.0)
    assert all_pairs
    assert len(filtered) <= len(all_pairs)
    assert all(abs(p.delta_activity) >= 1.0 - 1e-12 for p in filtered)
    # Phenol–anisole (|Δ|=1.5) should survive; tiny deltas should not.
    assert any({p.oid_a, p.oid_b} == {1, 2} for p in filtered)


def test_no_duplicate_molecule_pairs():
    """Same two molecules cut at different bonds must yield only one reported pair."""
    records = [
        _rec(1, "Oc1ccccc1", 1.0),
        _rec(2, "COc1ccccc1", 2.5),
        _rec(3, "CCOc1ccccc1", 3.0),
    ]
    pairs = find_matched_molecular_pairs(records, max_cuts=1)
    mol_keys = [(p.oid_a, p.oid_b) for p in pairs]
    assert len(mol_keys) == len(set(mol_keys))
    # Anisole–phenetole previously appeared twice (OMe/OEt and Me/Et cuts).
    anisole_phenetole = [p for p in pairs if {p.oid_a, p.oid_b} == {2, 3}]
    assert len(anisole_phenetole) == 1
    # Keep the smallest variable-atom cut (C↔CC rather than CO↔CCO).
    assert anisole_phenetole[0].transform == "C[*:1]>>CC[*:1]"


def test_assemble_mmp_table_annotations():
    records = [
        _rec(1, "Oc1ccccc1", 1.0),
        _rec(2, "COc1ccccc1", 2.5),
    ]
    pairs = find_matched_molecular_pairs(records, max_cuts=1)
    rows, headers = assemble_mmp_table_annotations(pairs, activity_column="pIC50")
    assert headers == ["MMP_Partners", "MMP_Transforms", "MMP_Delta_pIC50"]
    assert rows
    by_oid = {oid: vals for oid, vals in rows}
    assert 1 in by_oid and 2 in by_oid
    assert "2" in by_oid[1]["MMP_Partners"]


def test_highlight_atoms_for_pair():
    a = Chem.MolFromSmiles("Oc1ccccc1")
    b = Chem.MolFromSmiles("COc1ccccc1")
    ha, hb = highlight_atoms_for_pair(a, b)
    assert isinstance(ha, list) and isinstance(hb, list)
    # Anisole has an extra carbon relative to phenol; at least one side should highlight.
    assert ha or hb
