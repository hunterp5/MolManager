"""Tests for filter_compute SQL pushdown helpers."""

from __future__ import annotations

from pathlib import Path

from molmanager.filter_compute import build_sqlite_where, fetch_matching_oids
from molmanager.storage.sqlite_table_store import SqliteTableStore


def _store_with_rows(tmp_path: Path) -> SqliteTableStore:
    store = SqliteTableStore(tmp_path / "filter_test.sqlite3")
    headers = ["ID_HIDDEN", "Structure", "SMILES", "MW", "Phase"]
    rows = [
        (0, {"SMILES": "C", "MW": "16", "Phase": "prep"}),
        (1, {"SMILES": "CC", "MW": "30", "Phase": "ship"}),
        (2, {"SMILES": "CCC", "MW": "44", "Phase": "prep"}),
    ]
    store.rebuild(headers, rows)
    return store


def test_build_sqlite_where_numeric_range(tmp_path):
    store = _store_with_rows(tmp_path)
    where = build_sqlite_where(
        [{"kind": "numeric", "enabled": True, "column": "MW", "min": 20.0, "max": 40.0, "inverted": False}],
        headers=store.headers,
    )
    assert where is not None
    where_sql, args = where
    oids = fetch_matching_oids(store.db_path, where_sql, args)
    assert oids == frozenset({1})


def test_disabled_substructure_card_does_not_block_sqlite(tmp_path):
    store = _store_with_rows(tmp_path)
    where = build_sqlite_where(
        [
            {"kind": "substructure", "enabled": False},
            {"kind": "numeric", "enabled": True, "column": "MW", "min": 0.0, "max": 20.0, "inverted": False},
        ],
        headers=store.headers,
    )
    assert where is not None
    where_sql, args = where
    oids = fetch_matching_oids(store.db_path, where_sql, args)
    assert oids == frozenset({0})


def test_enabled_substructure_blocks_sqlite_pushdown(tmp_path):
    store = _store_with_rows(tmp_path)
    where = build_sqlite_where(
        [
            {"kind": "substructure", "enabled": True},
            {"kind": "numeric", "enabled": True, "column": "MW", "min": 0.0, "max": 100.0, "inverted": False},
        ],
        headers=store.headers,
    )
    assert where is None


def test_category_filter_in_clause(tmp_path):
    store = _store_with_rows(tmp_path)
    where = build_sqlite_where(
        [
            {
                "kind": "category",
                "enabled": True,
                "column": "Phase",
                "values": ["prep"],
            }
        ],
        headers=store.headers,
    )
    assert where is not None
    where_sql, args = where
    oids = fetch_matching_oids(store.db_path, where_sql, args)
    assert oids == frozenset({0, 2})
