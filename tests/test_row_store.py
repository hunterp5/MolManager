"""Tests for the row-text backing stores used by CompoundTableModel.

Every test runs against both the in-memory store and the SQLite-backed store to guarantee identical
semantics (the model relies on them being interchangeable).
"""

from __future__ import annotations

import pytest

from molmanager.ui.row_store import InMemoryRowStore, SqliteRowStore


@pytest.fixture(params=["memory", "sqlite"])
def store(request):
    if request.param == "memory":
        s = InMemoryRowStore()
    else:
        s = SqliteRowStore(cache_rows=2)  # tiny cache to exercise disk round-trips
    s.append_batch([(10, {"A": "1", "B": "x"}), (11, {"A": "2"}), (12, {"A": "3", "B": "z"})])
    yield s
    if isinstance(s, SqliteRowStore):
        s.clear()


def test_append_and_lookup(store):
    assert len(store) == 3
    assert store.all_oids() == [10, 11, 12]
    assert store.oid_at(1) == 11
    assert store.index_of(12) == 2
    assert store.index_of(999) == -1


def test_value_reads_default_for_missing(store):
    assert store.value_at(1, "A") == "2"
    assert store.value_at(1, "B") == ""
    assert store.value_by_oid(12, "B") == "z"
    assert store.value_by_oid(999, "A", "n/a") == "n/a"


def test_snapshot_pairs_and_column_by_oid(store):
    assert store.snapshot_pairs("A") == [(10, "1"), (11, "2"), (12, "3")]
    assert store.snapshot_pairs(None) == [(10, ""), (11, ""), (12, "")]
    assert store.column_by_oid("B") == {10: "x", 11: "", 12: "z"}


def test_set_value_paths(store):
    assert store.set_value_by_oid(11, "A", "20") == 1
    assert store.value_at(1, "A") == "20"
    assert store.set_value_by_oid(999, "A", "z") == -1
    store.set_value_at(0, "A", "100")
    assert store.value_at(0, "A") == "100"
    store.set_values_by_oid(12, {"A": "30", "B": "zz"})
    assert store.value_by_oid(12, "A") == "30"
    assert store.value_by_oid(12, "B") == "zz"


def test_set_column_and_bulk_return_changed_indices(store):
    changed = store.set_column_by_oids("A", [(10, "111"), (999, "x"), (12, "333")])
    assert sorted(changed) == [0, 2]
    assert store.value_at(0, "A") == "111"
    assert store.value_at(2, "A") == "333"
    changed2 = store.apply_columns_bulk({"A", "B"}, [(11, {"A": "22", "B": "y"})])
    assert changed2 == [1]
    assert store.value_at(1, "A") == "22"
    assert store.value_at(1, "B") == "y"


def test_fill_column_uses_default(store):
    store.fill_column("C", {11: "hit"}, default="miss")
    assert store.column_by_oid("C") == {10: "miss", 11: "hit", 12: "miss"}


def test_remove_by_oids_and_at(store):
    assert store.remove_by_oids({11}) == 1
    assert store.all_oids() == [10, 12]
    assert store.index_of(11) == -1
    assert store.remove_at(0) == 10
    assert store.all_oids() == [12]


def test_reorder_and_move(store):
    store.reorder([12, 10, 11])
    assert store.all_oids() == [12, 10, 11]
    assert store.index_of(10) == 1
    store.move(0, 2)
    assert store.all_oids() == [10, 11, 12]


def test_column_structure_ops(store):
    store.add_column("A2", copy_from="A")
    assert store.column_by_oid("A2") == {10: "1", 11: "2", 12: "3"}
    store.rename_column("A2", "A3")
    assert store.value_by_oid(10, "A3") == "1"
    assert store.value_by_oid(10, "A2") == ""
    store.remove_column("A3")
    assert store.column_by_oid("A3") == {10: "", 11: "", 12: ""}


def test_insert_many_at_restores_positions(store):
    store.remove_by_oids({11})
    store.insert_many_at([(1, 11, {"A": "2"})])
    assert store.all_oids() == [10, 11, 12]
    assert store.value_by_oid(11, "A") == "2"


def test_export_slice(store):
    assert store.export_slice(["A", "B"], 1, 3) == [
        (11, {"A": "2", "B": ""}),
        (12, {"A": "3", "B": "z"}),
    ]


def test_sqlite_store_survives_cache_eviction():
    """Values written beyond the tiny cache cap are still readable (came back from disk)."""
    s = SqliteRowStore(cache_rows=2)
    s.append_batch([(i, {"A": str(i)}) for i in range(50)])
    try:
        assert s.value_by_oid(0, "A") == "0"
        assert s.value_by_oid(49, "A") == "49"
        s.set_value_by_oid(0, "A", "zero")
        # Touch many other rows to evict oid 0 from the cache, then re-read from disk.
        for i in range(1, 50):
            s.value_by_oid(i, "A")
        assert s.value_by_oid(0, "A") == "zero"
    finally:
        s.clear()
