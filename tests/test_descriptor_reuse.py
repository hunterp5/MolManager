"""Tests for descriptor/fingerprint reuse helpers."""

from __future__ import annotations

from rdkit import Chem

from molmanager.descriptor_reuse import (
    column_complete_for_oids,
    is_valid_descriptor_cell,
    partition_descriptor_jobs,
)
from molmanager.fingerprint_cache import clear as clear_fp_cache
from molmanager.fingerprint_cache import store_from_mol
from molmanager.rdkit_fingerprints import (
    fingerprint_bitvect_for_row,
    spec_for_label,
)


def test_is_valid_descriptor_cell():
    assert is_valid_descriptor_cell("12.3")
    assert not is_valid_descriptor_cell("")
    assert not is_valid_descriptor_cell("N/A")


def test_partition_descriptor_jobs_skips_complete_columns():
    headers = ["ID_HIDDEN", "Structure", "Mol Weight"]
    oids = [1, 2]

    def row_for_oid(oid: int) -> int:
        return {1: 0, 2: 1}[oid]

    def cell_text(row: int, col: int) -> str:
        data = {
            (0, 2): "180.1",
            (1, 2): "46.0",
        }
        return data.get((row, col), "")

    disp, fns, out_hdrs, skipped = partition_descriptor_jobs(
        ["Mol Weight", "LogP"],
        ["MolWt", "MolLogP"],
        ["Mol Weight", "LogP"],
        oids,
        headers=headers,
        cell_text=cell_text,
        row_for_oid=row_for_oid,
    )
    assert skipped == ["Mol Weight"]
    assert disp == ["LogP"]
    assert fns == ["MolLogP"]
    assert out_hdrs == ["LogP"]


def test_fingerprint_bitvect_for_row_uses_cache():
    clear_fp_cache()
    mol = Chem.MolFromSmiles("CCO")
    spec = spec_for_label("Morgan (r=2, n=2048)")
    assert spec is not None
    store_from_mol(7, spec.internal_key, mol)
    fp = fingerprint_bitvect_for_row(7, mol, spec.label)
    assert fp is not None
    assert int(fp.GetNumOnBits()) > 0


def test_column_complete_for_oids_false_when_any_missing():
    headers = ["MW"]
    assert not column_complete_for_oids(
        "MW",
        [1, 2],
        headers=headers,
        cell_text=lambda row, col: "1.0" if row == 0 else "",
        row_for_oid=lambda oid: oid - 1,
    )
