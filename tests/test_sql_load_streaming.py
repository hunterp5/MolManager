from __future__ import annotations

import sqlite3

from molmanager.ui.main_window import ChemicalTableApp


def test_load_from_sql_streaming_sqlite(tmp_path, qapp):  # noqa: ARG001
    db_path = tmp_path / "sample.sqlite"
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute("CREATE TABLE compounds (SMILES TEXT, Note TEXT, MW REAL)")
        cur.execute("INSERT INTO compounds VALUES ('CCO', 'alpha', 46.07)")
        cur.execute("INSERT INTO compounds VALUES ('CCN', 'beta', 45.09)")
        cur.execute("INSERT INTO compounds VALUES ('CCC', 'gamma', 44.10)")
        con.commit()
    finally:
        con.close()

    w = ChemicalTableApp()
    url = "sqlite:///" + str(db_path).replace("\\", "/")
    w.load_from_sql(url=url, table="compounds", limit=10, apply_limit=True, clear_first=True)

    assert w._table_model.rowCount() == 3
    assert "SMILES" in w.headers
    assert w._table_model.value_for_header(0, "Note") == "alpha"
    assert w._table_model.value_for_header(1, "Note") == "beta"
    w.close()

