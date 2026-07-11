"""Tests for table structure rendering."""

from __future__ import annotations

from molmanager.display_constants import (
    STRUCTURE_DEPICT_BOND_LINE_WIDTH,
    STRUCTURE_DEPICT_HEIGHT,
    STRUCTURE_DEPICT_WIDTH,
)
from molmanager.structure_draw import render_molecule_png, structure_cairo_dimensions


def test_structure_cairo_dimensions_match_target() -> None:
    cw, ch = structure_cairo_dimensions(STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT)
    assert cw == STRUCTURE_DEPICT_WIDTH
    assert ch == STRUCTURE_DEPICT_HEIGHT


def test_structure_cairo_dimensions_zoomed() -> None:
    cw, ch = structure_cairo_dimensions(STRUCTURE_DEPICT_WIDTH * 2, STRUCTURE_DEPICT_HEIGHT * 2)
    assert cw == STRUCTURE_DEPICT_WIDTH * 2
    assert ch == STRUCTURE_DEPICT_HEIGHT * 2


def test_render_molecule_png_returns_bytes() -> None:
    from rdkit import Chem

    mol = Chem.MolFromSmiles("c1ccccc1")
    png = render_molecule_png(mol, STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT)
    assert isinstance(png, (bytes, bytearray))
    assert len(png) > 100


def test_render_molecule_png_native_resolution() -> None:
    """Table renders are 1× (no supersample) for Render2D throughput."""
    from rdkit import Chem

    mol = Chem.MolFromSmiles("c1ccccc1")
    png = render_molecule_png(mol, STRUCTURE_DEPICT_WIDTH, STRUCTURE_DEPICT_HEIGHT)
    # PNG IHDR width/height are big-endian at bytes 16:24.
    assert int.from_bytes(png[16:20], "big") == STRUCTURE_DEPICT_WIDTH
    assert int.from_bytes(png[20:24], "big") == STRUCTURE_DEPICT_HEIGHT


def test_table_bond_line_width_constant() -> None:
    assert STRUCTURE_DEPICT_BOND_LINE_WIDTH < 2.0
