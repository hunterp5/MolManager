"""Tests for memory-safety hardening (leaks, caps, PNG/store cleanup)."""

from __future__ import annotations

from molmanager.fingerprint_cache import clear as clear_fp_cache
from molmanager.fingerprint_cache import store as store_fp
from molmanager.fingerprint_cache import get as get_fp
from molmanager.memory_guards import (
    check_cluster_workload,
    check_conformer_workload,
    check_fp_matrix_workload,
    check_product_enumeration,
)
from molmanager.structure_render_store import StructureRenderStore
from molmanager.storage.sqlite_table_store import SqliteTableStore


def test_fingerprint_cache_clear_empties_store() -> None:
    clear_fp_cache()
    store_fp(1, "test_key", object())
    assert get_fp(1, "test_key") is not None
    clear_fp_cache()
    assert get_fp(1, "test_key") is None


def test_structure_render_store_png_entry_cap() -> None:
    store = StructureRenderStore(max_decoded_pixmaps=4, max_png_entries=2)
    store.ingest_batch([(1, b"a"), (2, b"b"), (3, b"c")])
    assert len(store) == 2
    assert not store.has_png(1)
    assert store.has_png(2)
    assert store.has_png(3)


def test_structure_render_store_png_bytes_roundtrip() -> None:
    store = StructureRenderStore(max_decoded_pixmaps=4)
    store.ingest_png(9, b"png-bytes")
    assert store.png_bytes(9) == b"png-bytes"
    store.remove_oid(9)
    assert store.png_bytes(9) is None


def test_sqlite_stream_rebuild_without_entries_list(tmp_path) -> None:
    db = tmp_path / "stream.sqlite3"
    store = SqliteTableStore(db)
    headers = ["ID_HIDDEN", "Structure", "Name"]
    store.start_stream_rebuild(headers)
    store.append_stream_rows([(1, {"Name": "a"}), (2, {"Name": "b"})])
    store.append_stream_rows([(3, {"Name": "c"})])
    store.finish_stream_rebuild()
    assert store.count() == 3
    store.close()


def test_memory_guards_block_oversized_workloads(monkeypatch) -> None:
    monkeypatch.setenv("MOLMANAGER_MEMORY_GUARD_CONF_MAX_ROWS", "10")
    monkeypatch.setenv("MOLMANAGER_MEMORY_GUARD_CONF_MAX_ROW_CONFS", "100")
    monkeypatch.setenv("MOLMANAGER_MEMORY_GUARD_CLUSTER_MAX_ROWS", "150")
    monkeypatch.setenv("MOLMANAGER_MEMORY_GUARD_FP_MATRIX_MAX_CELLS", "100000")
    monkeypatch.setenv("MOLMANAGER_MEMORY_GUARD_ENUM_MAX_PRODUCTS", "50")

    assert not check_conformer_workload(11, 1).ok
    assert not check_conformer_workload(5, 30).ok
    assert check_conformer_workload(2, 10).ok

    assert not check_cluster_workload(151).ok
    assert check_cluster_workload(5).ok

    assert not check_fp_matrix_workload(200, n_bits=2048).ok
    assert check_fp_matrix_workload(2, n_bits=64).ok

    assert not check_product_enumeration(51).ok
    assert check_product_enumeration(10).ok
