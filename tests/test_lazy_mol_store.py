"""Bounded, disk-spilling molecule store used for ChemicalTableApp.mols."""

from __future__ import annotations

import os

from rdkit import Chem
from rdkit.Chem import AllChem

from molmanager.lazy_mol_store import LazyMolStore


def _mol(smiles: str) -> Chem.Mol:
    m = Chem.MolFromSmiles(smiles)
    assert m is not None
    return m


def test_lazy_mol_store_basic_dict_semantics():
    store = LazyMolStore(max_live=0)
    store[1] = _mol("CCO")
    store[2] = _mol("CCN")
    assert len(store) == 2
    assert 1 in store and 2 in store and 3 not in store
    assert store.get(3) is None
    assert Chem.MolToSmiles(store[1]) == Chem.MolToSmiles(_mol("CCO"))
    assert set(store) == {1, 2}
    assert {oid for oid, _ in store.items()} == {1, 2}
    popped = store.pop(1)
    assert Chem.MolToSmiles(popped) == Chem.MolToSmiles(_mol("CCO"))
    assert 1 not in store and len(store) == 1
    assert store.pop(999, None) is None


def test_lazy_mol_store_spills_beyond_cap_and_retrieves():
    store = LazyMolStore(max_live=2)
    smis = {oid: smi for oid, smi in enumerate(["C", "CC", "CCC", "CCCC", "CCCCC"], start=1)}
    for oid, smi in smis.items():
        store[oid] = _mol(smi)
    assert len(store) == 5
    assert len(store._live) <= 2
    assert set(store._live) & store._disk_oids == set()
    assert set(store._live) | store._disk_oids == set(smis)
    for oid, smi in smis.items():
        assert oid in store
        assert Chem.MolToSmiles(store[oid]) == Chem.MolToSmiles(_mol(smi))
    # items() must surface spilled molecules too.
    assert {oid for oid, _ in store.items()} == set(smis)
    store.clear()


def test_lazy_mol_store_spill_preserves_props_and_conformers():
    store = LazyMolStore(max_live=1)
    m = Chem.AddHs(_mol("CCO"))
    AllChem.EmbedMolecule(m, randomSeed=1)
    m.SetProp("_Name", "ethanol")
    m.SetProp("Batch", "A42")
    store[1] = m
    store[2] = _mol("CC")  # forces oid 1 to spill to disk
    assert 1 in store._disk_oids
    got = store[1]
    assert got is not None
    assert got.GetProp("_Name") == "ethanol"
    assert got.GetProp("Batch") == "A42"
    assert got.GetNumConformers() == 1
    store.clear()


def test_lazy_mol_store_reingest_supersedes_disk_copy():
    store = LazyMolStore(max_live=1)
    store[1] = _mol("CCO")
    store[2] = _mol("CC")
    assert 1 in store._disk_oids
    store[1] = _mol("c1ccccc1")
    assert 1 in store._live and 1 not in store._disk_oids
    assert Chem.MolToSmiles(store[1]) == Chem.MolToSmiles(_mol("c1ccccc1"))
    store.clear()


def test_lazy_mol_store_clear_removes_temp_file():
    store = LazyMolStore(max_live=1)
    store[1] = _mol("CCO")
    store[2] = _mol("CC")
    path = store._disk_path
    assert path and os.path.exists(path)
    store.clear()
    assert not os.path.exists(path)
    assert len(store) == 0


def test_lazy_mol_store_unbounded_when_cap_zero():
    store = LazyMolStore(max_live=0)
    for oid in range(1, 11):
        store[oid] = _mol("C" * (oid % 4 + 1))
    assert len(store._live) == 10
    assert store._disk_oids == set()
    assert store._disk is None
