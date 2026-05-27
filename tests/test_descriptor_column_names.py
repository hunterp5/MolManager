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
