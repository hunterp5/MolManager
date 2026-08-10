"""Lone-pair count helpers for sketcher display."""

from __future__ import annotations

from molmanager.ui.sketcher.chem import sketch_lone_pair_count


def test_sketch_lone_pair_counts() -> None:
    # Water / alcohol O (one drawn bond + one implicit H)
    assert sketch_lone_pair_count("O", bond_order_sum=1, implicit_h=1) == 2
    # Carbonyl / ether O
    assert sketch_lone_pair_count("O", bond_order_sum=2, implicit_h=0) == 2
    # Ammonia N
    assert sketch_lone_pair_count("N", bond_order_sum=3, implicit_h=0) == 1
    # Fluorine
    assert sketch_lone_pair_count("F", bond_order_sum=1, implicit_h=0) == 3
    # Carbon / hydrogen
    assert sketch_lone_pair_count("C", bond_order_sum=4, implicit_h=0) == 0
    assert sketch_lone_pair_count("H", bond_order_sum=1, implicit_h=0) == 0
    # Charged oxygen (alkoxide): one bond, no H
    assert sketch_lone_pair_count("O", formal_charge=-1, bond_order_sum=1, implicit_h=0) == 3
