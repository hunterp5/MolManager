"""LazyMolStore blob access without full rehydration."""

from __future__ import annotations

from rdkit import Chem

from molmanager.lazy_mol_store import LazyMolStore


def test_get_blob_reads_spilled_entry_without_promoting_to_ram():
    store = LazyMolStore(max_live=2)
    mols = [Chem.MolFromSmiles(smi) for smi in ("C", "CC", "CCC")]
    for i, mol in enumerate(mols, start=1):
        store[i] = mol
    blob = store.get_blob(1)
    assert blob is not None
    assert 1 not in store._live
    assert 1 in store._disk_oids
