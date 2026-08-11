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

"""Unique column names for descriptor calculation."""

from __future__ import annotations

from molmanager.ui.main_window.chemistry_mixin import ChemistryMixin


class _Host(ChemistryMixin):
    def __init__(self, headers: list[str]) -> None:
        self.headers = list(headers)


def test_unique_table_column_names_skips_existing() -> None:
    host = _Host(["ID_HIDDEN", "Structure", "LogP", "LogP (1)"])
    names = host._unique_table_column_names(["LogP", "TPSA"])
    assert names == ["LogP (2)", "TPSA"]
