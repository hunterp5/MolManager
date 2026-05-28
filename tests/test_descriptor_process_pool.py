"""Descriptor worker process-pool thresholds and batched fingerprint calculation."""

from __future__ import annotations

from rdkit import Chem

from molmanager.config import load_config
from molmanager.rdkit_fingerprints import int_fns_include_fingerprints
from molmanager.workers.chemistry_tools import (
    _descriptor_process_pool_min_rows,
    _mp_calc_descriptor_batch,
)


def test_int_fns_include_fingerprints():
    assert int_fns_include_fingerprints(["FP_Morgan_2_2048", "MolWt"])
    assert not int_fns_include_fingerprints(["MolWt", "SMILES"])


def test_descriptor_pool_min_rows_morgan_uses_fp_threshold():
    cfg = load_config()
    assert _descriptor_process_pool_min_rows(cfg, ["FP_Morgan_2_2048"]) == cfg.descriptor_fp_process_pool_min_rows


def test_descriptor_pool_min_rows_pharm2d_is_two():
    cfg = load_config()
    assert _descriptor_process_pool_min_rows(cfg, ["FP_Pharm2D_Gobbi"]) == 2


def test_descriptor_pool_min_rows_plain_descriptors_use_default(monkeypatch):
    monkeypatch.setenv("MOLMANAGER_DESCRIPTOR_PROCESS_POOL_MIN_ROWS", "999")
    cfg = load_config()
    assert _descriptor_process_pool_min_rows(cfg, ["MolWt"]) == 999


def test_mp_calc_descriptor_batch_fingerprint_rows():
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    blob = mol.ToBinary()
    items = [(10, blob, None), (11, blob, None)]
    rows = _mp_calc_descriptor_batch(
        (items, ("Morgan on-bits",), ("FP_Morgan_2_2048",), False)
    )
    assert len(rows) == 2
    assert rows[0][0] == 10
    assert rows[1][0] == 11
    assert int(rows[0][1]["Morgan on-bits"]) > 0
    assert rows[0][1]["Morgan on-bits"] == rows[1][1]["Morgan on-bits"]
