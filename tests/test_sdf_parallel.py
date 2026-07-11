"""Tests for parallel SDF ingest helpers."""

from __future__ import annotations

from rdkit import Chem

from molmanager.sdf_parallel import iter_sdf_molblocks, mp_parse_sdf_molblocks


def _write_two_record_sdf(path) -> None:
    m1 = Chem.MolFromSmiles("CCO")
    m1.SetProp("Note", "alpha")
    m2 = Chem.MolFromSmiles("CCN")
    m2.SetProp("Note", "beta")
    w = Chem.SDWriter(str(path))
    try:
        w.write(m1)
        w.write(m2)
    finally:
        w.close()


def test_iter_sdf_molblocks_yields_records(tmp_path):
    sdf_path = tmp_path / "sample.sdf"
    _write_two_record_sdf(sdf_path)
    blocks = list(iter_sdf_molblocks(str(sdf_path)))
    assert len(blocks) == 2


def test_mp_parse_sdf_molblocks_preserves_sd_tags(tmp_path):
    sdf_path = tmp_path / "sample.sdf"
    _write_two_record_sdf(sdf_path)
    blocks = list(iter_sdf_molblocks(str(sdf_path)))
    blobs = mp_parse_sdf_molblocks(blocks)
    assert len(blobs) == 2
    assert blobs[0] is not None
    mol = Chem.Mol(blobs[0])
    assert mol.GetProp("Note") == "alpha"
