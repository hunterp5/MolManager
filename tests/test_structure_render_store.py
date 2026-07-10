from __future__ import annotations

import os

from molmanager.structure_render_store import StructureRenderStore


def test_structure_render_store_ingest_remove_and_trim():
    store = StructureRenderStore(max_decoded_pixmaps=4)
    store.ingest_batch([(1, b"a"), (2, b"b"), (3, b"c")])
    assert len(store) == 3
    assert store.has_png(2)
    store.remove_oid(2)
    assert not store.has_png(2)
    store.trim_decoded_cache(keep_oids={1})
    assert len(store._lru) == 0


def test_structure_render_store_spills_to_disk_beyond_cap():
    store = StructureRenderStore(max_png_ram_rows=2)
    payloads = {oid: f"png{oid}".encode() for oid in range(1, 6)}
    store.ingest_batch(list(payloads.items()))
    # Total count is preserved across RAM + disk, and the two sets are disjoint.
    assert len(store) == 5
    assert len(store._png) <= 2
    assert set(store._png) & store._disk_oids == set()
    assert set(store._png) | store._disk_oids == set(payloads)
    # Every oid — including spilled ones — is retrievable with the correct bytes.
    for oid, data in payloads.items():
        assert store.has_png(oid)
        assert store._bytes_for(oid) == data
    store.clear()


def test_structure_render_store_reingest_pulls_back_from_disk():
    store = StructureRenderStore(max_png_ram_rows=1)
    store.ingest_batch([(1, b"one"), (2, b"two")])
    # oid 1 was spilled; re-ingesting must supersede the disk copy (sets stay disjoint).
    assert 1 in store._disk_oids
    store.ingest_png(1, b"one-v2")
    assert 1 in store._png
    assert 1 not in store._disk_oids
    assert store._bytes_for(1) == b"one-v2"
    store.clear()


def test_structure_render_store_clear_removes_temp_file():
    store = StructureRenderStore(max_png_ram_rows=1)
    store.ingest_batch([(1, b"a"), (2, b"b"), (3, b"c")])
    path = store._disk_path
    assert path and os.path.exists(path)
    store.clear()
    assert not os.path.exists(path)
    assert len(store) == 0


def test_structure_render_store_unbounded_when_cap_zero():
    store = StructureRenderStore(max_png_ram_rows=0)
    store.ingest_batch([(oid, f"p{oid}".encode()) for oid in range(1, 11)])
    assert len(store._png) == 10
    assert store._disk_oids == set()
    assert store._disk is None
