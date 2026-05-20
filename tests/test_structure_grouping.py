"""Structure deduplication for pkasolver-backed workers."""

from __future__ import annotations

from rdkit import Chem

from chemmanager.workers.structure_grouping import group_rows_by_structure


def test_group_rows_by_structure_deduplicates_identical_smiles() -> None:
    m1 = Chem.MolFromSmiles("CCO")
    m2 = Chem.MolFromSmiles("CCO")
    assert m1 is not None and m2 is not None
    rows = [(10, m1), (20, m2), (30, Chem.MolFromSmiles("CCN"))]
    order, rep, oids_map = group_rows_by_structure(rows)
    assert len(order) == 2
    assert len(oids_map[order[0]]) == 2
    assert 10 in oids_map[order[0]] and 20 in oids_map[order[0]]
    assert oids_map[order[1]] == [30]
