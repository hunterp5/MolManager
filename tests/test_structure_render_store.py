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
