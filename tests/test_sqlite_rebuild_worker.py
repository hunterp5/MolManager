"""SqliteRebuildWorker (background SQLite mirror rebuild)."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QThreadPool

from molmanager.storage import SqliteTableStore
from molmanager.workers import SqliteRebuildSignals, SqliteRebuildWorker


def test_sqlite_rebuild_worker_builds_queryable_db(qapp, tmp_path):  # noqa: ARG001
    sig = SqliteRebuildSignals()
    results: list[tuple[int, str]] = []

    sig.finished.connect(lambda gen, path: results.append((gen, path)))
    db_path = str(tmp_path / "mirror.sqlite3")
    headers = ["ID_HIDDEN", "Structure", "SMILES", "Score"]
    entries = [
        (1, {"SMILES": "CCO", "Score": "1.0"}),
        (2, {"SMILES": "CCN", "Score": "9.0"}),
    ]
    pool = QThreadPool()
    pool.start(SqliteRebuildWorker(3, headers, entries, db_path, sig))
    assert pool.waitForDone(60_000)
    qapp.processEvents()
    assert results and results[0][0] == 3
    store = SqliteTableStore(Path(results[0][1]))
    try:
        assert store.count() == 2
        assert store.count(where_sql='CAST("Score" AS REAL) >= ?', args=(5.0,)) == 1
    finally:
        store.close()
