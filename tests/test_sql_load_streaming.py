# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

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

