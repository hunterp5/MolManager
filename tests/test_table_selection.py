"""Table selection helpers."""

from chemmanager.ui.compound_table_model import CompoundTableModel
from chemmanager.ui.table_selection import merge_sorted_row_indices


def test_merge_sorted_row_indices_contiguous():
    assert merge_sorted_row_indices([0, 1, 2, 5, 6]) == [(0, 2), (5, 6)]


def test_merge_sorted_row_indices_single():
    assert merge_sorted_row_indices([42]) == [(42, 42)]


def test_invert_row_indices():
    n = 10
    selected = {1, 2, 3}
    inverted = [r for r in range(n) if r not in selected]
    assert inverted == [0, 4, 5, 6, 7, 8, 9]


def test_compound_model_highlighted_oids():
    model = CompoundTableModel(["ID_HIDDEN", "Structure", "MW"])
    model.append_row(1, {"MW": "1"})
    model.append_row(2, {"MW": "2"})
    assert not model.is_row_highlighted(0)
    model.set_highlighted_oids(frozenset({1}))
    assert model.is_row_highlighted(0)
    assert not model.is_row_highlighted(1)
    model.set_highlighted_oids(None)
    assert not model.is_row_highlighted(0)
