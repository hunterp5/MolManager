from __future__ import annotations

from chemmanager.storage import SqliteTableStore


def test_sqlite_table_store_distinct_values():
    from chemmanager.storage.sqlite_table_store import SqliteTableStore

    store = SqliteTableStore()
    store.rebuild(
        ["ID_HIDDEN", "Structure", "MW"],
        [(1, {"MW": "100"}), (2, {"MW": "200"}), (3, {"MW": "100"})],
    )
    vals = store.distinct_values("MW", limit=10)
    assert vals == ["100", "200"]


def test_sqlite_table_store_rebuild_and_filter_page():
    store = SqliteTableStore()
    try:
        rows = [
            (1, {"SMILES": "CCO", "Name": "alpha", "Score": "3.2"}),
            (2, {"SMILES": "CCN", "Name": "beta", "Score": "7.1"}),
            (3, {"SMILES": "CCC", "Name": "alphabet", "Score": "9.9"}),
        ]
        store.rebuild(["ID_HIDDEN", "Structure", "SMILES", "Name", "Score"], rows)
        assert store.count() == 3
        assert store.count(where_sql='CAST("Score" AS REAL) >= ?', args=(7.0,)) == 2
        page = store.fetch_page(
            limit=10,
            where_sql='LOWER("Name") LIKE ?',
            args=("%alpha%",),
            sort_by="Score",
            ascending=False,
        )
        assert [oid for oid, _ in page] == [3, 1]
    finally:
        store.close()

