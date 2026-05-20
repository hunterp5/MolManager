"""Conformer generation core (no Qt)."""

from __future__ import annotations

import base64
import json
import threading

from rdkit import Chem

from chemmanager.confs_codec import unpack_confs_blocks_json_b64
from chemmanager.workers import ConformerGenParams, format_confs_table_cell, pack_confs_cell, run_conformer_generation


def test_run_conformer_generation_ethanol_mmff():
    m = Chem.MolFromSmiles("CCO")
    p = ConformerGenParams(
        num_confs=4,
        energy_window_kcal=100.0,
        force_field="MMFF",
        random_seed=7,
        prune_rms_threshold=-1.0,
        max_iterations=100,
    )
    out, meta = run_conformer_generation(m, p)
    assert out is not None
    assert meta.get("ok") is True
    assert meta.get("n_embedded", 0) >= 1
    assert out.GetNumConformers() == meta.get("n_kept")
    cell = format_confs_table_cell(meta)
    d = json.loads(cell)
    assert d["ok"] is True
    assert d["ff"] == "MMFF"
    assert len(cell) < 500

    packed = pack_confs_cell(meta, out)
    b64 = unpack_confs_blocks_json_b64(packed)
    assert b64 is not None
    blocks = json.loads(base64.b64decode(b64.encode("ascii")))
    assert isinstance(blocks, list)
    assert len(blocks) == meta.get("n_kept")


def test_run_conformer_generation_empty_mol():
    m = Chem.Mol()
    p = ConformerGenParams(num_confs=2, energy_window_kcal=1.0, force_field="UFF", random_seed=1, max_iterations=50)
    out, meta = run_conformer_generation(m, p)
    assert out is None
    assert meta.get("ok") is False
    assert "empty" in (meta.get("err") or "").lower()


def test_unpack_confs_legacy_meta_only_cell():
    cell = json.dumps({"ok": True, "n_kept": 3, "ff": "MMFF"}, separators=(",", ":"))
    assert unpack_confs_blocks_json_b64(cell) is None


def test_run_conformer_generation_single_lowest_energy():
    m = Chem.MolFromSmiles("CCO")
    p = ConformerGenParams.single_lowest_energy(force_field="UFF", random_seed=3, max_iterations=80)
    out, meta = run_conformer_generation(m, p)
    assert out is not None
    assert meta.get("ok") is True
    assert meta.get("n_requested") == 1
    assert out.GetNumConformers() == 1
    assert meta.get("n_kept") == 1
    packed = pack_confs_cell(meta, out)
    assert unpack_confs_blocks_json_b64(packed) is not None


def test_run_conformer_generation_cancelled_before_work():
    m = Chem.MolFromSmiles("CCO")
    p = ConformerGenParams(
        num_confs=4,
        energy_window_kcal=100.0,
        force_field="MMFF",
        random_seed=7,
        prune_rms_threshold=-1.0,
        max_iterations=50,
    )
    ev = threading.Event()
    ev.set()
    out, meta = run_conformer_generation(m, p, cancel_event=ev)
    assert out is None
    assert meta.get("err") == "cancelled"
