from __future__ import annotations

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


def test_structure_render_store_evicts_oldest_png_when_capped():
    store = StructureRenderStore(max_decoded_pixmaps=8, max_png_entries=3)
    for i in range(1, 5):
        store.ingest_png(i, str(i).encode())
    assert len(store) == 3
    assert list(store._png.keys()) == [2, 3, 4]


def test_structure_render_store_batch_expands_cap_to_fit():
    store = StructureRenderStore(max_decoded_pixmaps=8, max_png_entries=3)
    items = [(i, str(i).encode()) for i in range(1, 6)]
    store.ingest_batch(items)
    assert len(store) == 5
    assert store.has_png(1) and store.has_png(5)
