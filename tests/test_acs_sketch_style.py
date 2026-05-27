"""ACS 1996 sketch style parameters."""

from __future__ import annotations

from molmanager.ui.sketcher.acs_style import acs_sketch_style
from molmanager.ui.sketcher.constants import SKETCH_MEDIAN_BOND_PX


def test_acs_sketch_style_default_matches_sketch_bond_length() -> None:
    style = acs_sketch_style()
    assert style.median_bond_px == float(SKETCH_MEDIAN_BOND_PX)


def test_acs_sketch_style_scales_with_bond_length() -> None:
    small = acs_sketch_style(40.0)
    large = acs_sketch_style(80.0)
    assert large.label_font_pt >= small.label_font_pt
    assert large.double_bond_offset_px > small.double_bond_offset_px


def test_acs_sketch_style_readable_label_size() -> None:
    style = acs_sketch_style(SKETCH_MEDIAN_BOND_PX)
    assert style.label_font_pt >= 11
