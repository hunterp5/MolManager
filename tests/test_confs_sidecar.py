"""Sidecar storage for conformer table cells (lightweight cells + in-memory blocks)."""

from rdkit import Chem
from rdkit.Chem import AllChem

from chemmanager.confs_codec import (
    demote_v1_cell_to_sidecar,
    deserialize_confs_sidecar,
    mol_from_packed_confs_cell,
    pack_confs_cell,
    rehydrate_v1_confs_cell,
    resolve_blocks_b64_for_viewer,
    serialize_confs_sidecar,
    unpack_confs_blocks_json_b64,
)
from chemmanager.workers import ConformerGenParams, run_conformer_generation


def _simple_mol():
    m = Chem.MolFromSmiles("CCO")
    AllChem.EmbedMolecule(m, randomSeed=0xf00d)
    return m


def test_demote_then_resolve_and_rehydrate_roundtrip():
    p = ConformerGenParams(
        num_confs=4,
        energy_window_kcal=100.0,
        force_field="UFF",
        random_seed=2,
        max_iterations=40,
    )
    m0 = _simple_mol()
    out, meta = run_conformer_generation(Chem.Mol(m0), p)
    assert out is not None and out.GetNumConformers() >= 2
    packed = pack_confs_cell(meta, out)
    assert unpack_confs_blocks_json_b64(packed) is not None

    light, b64 = demote_v1_cell_to_sidecar(packed, "confs")
    assert b64 is not None and b64 == unpack_confs_blocks_json_b64(packed)
    assert len(light) < len(packed) // 2
    assert unpack_confs_blocks_json_b64(light) is None

    oid = 42
    store = {(oid, "confs"): b64}
    assert resolve_blocks_b64_for_viewer(light, "confs", oid, store) == b64
    assert resolve_blocks_b64_for_viewer(light, "superpose", oid, store) is None

    full = rehydrate_v1_confs_cell(light, "confs", oid, store)
    assert unpack_confs_blocks_json_b64(full) == b64
    mol2 = mol_from_packed_confs_cell(full)
    assert mol2 is not None
    assert mol2.GetNumConformers() == out.GetNumConformers()


def test_serialize_deserialize_sidecar_roundtrip():
    store = {(1, "confs"): "YWFh", (2, "superpose"): "YmJi"}
    raw = serialize_confs_sidecar(store)
    back = deserialize_confs_sidecar(raw)
    assert back == store
