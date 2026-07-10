"""Tests for background/async table sorting: shared key logic and model apply path."""

from __future__ import annotations

import pytest
from PyQt5.QtCore import Qt

from molmanager.table_sort import build_sort_order
from molmanager.ui.compound_table_model import CompoundTableModel


def test_build_sort_order_numeric_puts_non_numeric_last():
    pairs = [(1, "10"), (2, "2"), (3, "abc"), (4, "1.5")]
    order = build_sort_order(list(pairs), column=3, sort_kind="numeric", reverse=False)
    assert order == [4, 2, 1, 3]


def test_build_sort_order_numeric_reverse():
    pairs = [(1, "10"), (2, "2"), (4, "1.5")]
    order = build_sort_order(list(pairs), column=3, sort_kind="numeric", reverse=True)
    assert order == [1, 2, 4]


def test_build_sort_order_alphabetic_case_insensitive():
    pairs = [(1, "Banana"), (2, "apple"), (3, "cherry")]
    order = build_sort_order(list(pairs), column=2, sort_kind="alphabetic", reverse=False)
    assert order == [2, 1, 3]


def test_build_sort_order_oid_column_uses_oid():
    pairs = [(3, ""), (1, ""), (2, "")]
    order = build_sort_order(list(pairs), column=0, sort_kind="numeric", reverse=False)
    assert order == [1, 2, 3]


@pytest.fixture()
def model(qapp):  # noqa: ARG001 — ensures QApplication exists for Qt types
    m = CompoundTableModel(["ID_HIDDEN", "Structure", "SMILES", "MW"])
    for oid, mw in [(10, "30.5"), (11, "12.0"), (12, "x"), (13, "5")]:
        m.append_row(oid, {"SMILES": "C", "MW": mw})
    return m


def test_model_sort_matches_apply_sorted_oids(model: CompoundTableModel):
    pairs = model.snapshot_sort_pairs(3)
    order = build_sort_order(list(pairs), 3, "numeric", reverse=False)
    model.apply_sorted_oids(order)
    assert model.all_oids_in_order() == [13, 11, 10, 12]


def test_model_sort_delegates_to_shared_helper(model: CompoundTableModel):
    """The synchronous ``sort`` must produce the same order as the async pair-based path."""
    model.sort(3, Qt.AscendingOrder, sort_kind="numeric")
    assert model.all_oids_in_order() == [13, 11, 10, 12]


def test_apply_sorted_oids_handles_missing_oids(model: CompoundTableModel):
    """Rows not present in the precomputed order fall to the end (order changed since snapshot)."""
    model.apply_sorted_oids([13, 11])
    ordered = model.all_oids_in_order()
    assert ordered[:2] == [13, 11]
    assert set(ordered[2:]) == {10, 12}
