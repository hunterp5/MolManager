"""Wedge/hash bond tuple orientation: tip (first atom) must not be multiply-bonded."""

from molmanager.ui.sketcher.bonds import _bond_make, reorient_wedged_bonds_tip_away_from_multiples


def test_reorient_swaps_when_tip_on_double_bonded_atom():
    # Nodes: 0 = sp3 (chiral side), 1 = alkene C (incident double to 2), 2 = other alkene C
    bonds = [
        _bond_make(1, 0, 1, 1),  # wrong: wedge tip (1) on alkene carbon
        _bond_make(1, 2, 2, 0),
    ]
    out = reorient_wedged_bonds_tip_away_from_multiples(bonds)
    assert out[0] == _bond_make(0, 1, 1, 1)
    assert out[1] == bonds[1]


def test_reorient_keeps_tip_on_sp3_when_base_is_alkene():
    bonds = [
        _bond_make(0, 1, 1, 2),  # hash: tip at 0 (sp3), base at 1 (alkene)
        _bond_make(1, 2, 2, 0),
    ]
    out = reorient_wedged_bonds_tip_away_from_multiples(bonds)
    assert out[0] == bonds[0]


def test_reorient_strips_when_both_endpoints_multiply_bonded():
    bonds = [
        _bond_make(3, 4, 1, 1),  # hypothetical single between two alkene-like centers
        _bond_make(3, 1, 2, 0),
        _bond_make(4, 2, 2, 0),
    ]
    out = reorient_wedged_bonds_tip_away_from_multiples(bonds)
    assert out[0] == _bond_make(3, 4, 1, 0)
