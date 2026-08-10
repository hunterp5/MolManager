"""Periodic-table element parsing for the sketcher ? tool."""

from __future__ import annotations

from molmanager.ui.sketcher.chem import _parse_atom_symbol_input, _parse_periodic_element_symbol


def test_parse_periodic_element_symbol_accepts_toolbar_and_extra() -> None:
    assert _parse_periodic_element_symbol("Au") == "Au"
    assert _parse_periodic_element_symbol("ru") == "Ru"
    assert _parse_periodic_element_symbol("SE") == "Se"
    assert _parse_periodic_element_symbol("C") == "C"
    assert _parse_periodic_element_symbol("*") is None
    assert _parse_periodic_element_symbol("?") is None
    assert _parse_periodic_element_symbol("Xx") is None


def test_parse_atom_symbol_wildcard_star_only() -> None:
    assert _parse_atom_symbol_input("*") == ("*", None)
    assert _parse_atom_symbol_input("?") is None
    assert _parse_atom_symbol_input("Au") == ("Au", None)
