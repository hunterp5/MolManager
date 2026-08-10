"""Bond highlight underlay sizing for multi-order bonds."""

from __future__ import annotations

from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QColor, QImage, QPainter

from molmanager.ui.sketcher.acs_style import acs_sketch_style
from molmanager.ui.sketcher.bonds import _bond_make
from molmanager.ui.sketcher.widget import SketchWidget


def test_bond_highlight_width_covers_multiple_bonds(qapp) -> None:  # noqa: ARG001
    w = SketchWidget()
    style = acs_sketch_style()
    single = w._bond_highlight_stroke_width(style, 1, selected=True)
    double = w._bond_highlight_stroke_width(style, 2, selected=True)
    triple = w._bond_highlight_stroke_width(style, 3, selected=True)
    assert double > single
    assert triple >= double
    assert double >= style.double_bond_offset_px


def test_double_bond_highlight_does_not_redraw_as_blue_double(qapp) -> None:  # noqa: ARG001
    """Highlights use a translucent centerline underlay, not thickened multi-line bonds."""
    w = SketchWidget()
    w.nodes = [
        {"id": 1, "pos": QPoint(40, 40), "element": "C"},
        {"id": 2, "pos": QPoint(100, 40), "element": "C"},
    ]
    w.bonds = [_bond_make(1, 2, 2, 0)]
    w.selected_bond_indices = {0}
    style = w._acs_style()
    img = QImage(140, 80, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    w._paint_sketch_bond_highlights(p, style)
    p.end()
    # Sample near the bond axis: underlay should leave a bluish tint.
    c = img.pixelColor(70, 40)
    assert c.blue() > c.red()
