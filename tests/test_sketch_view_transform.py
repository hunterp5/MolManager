"""Sketcher view zoom transform (proportional paint scaling)."""

from __future__ import annotations

from PyQt5.QtCore import QPoint

from chemmanager.ui.sketcher.widget import SketchWidget


def test_widget_model_roundtrip_at_unit_scale(qapp) -> None:
    w = SketchWidget()
    w.resize(400, 400)
    w._view_scale = 1.0
    p = QPoint(120, 80)
    assert w._widget_point_to_model(p) == p


def test_zoom_scales_model_inverse(qapp) -> None:
    w = SketchWidget()
    w.resize(400, 400)
    w._view_scale = 2.0
    c = w.rect().center()
    p = QPoint(c.x() + 40, c.y() + 20)
    m = w._widget_point_to_model(p)
    assert m.x() == c.x() + 20
    assert m.y() == c.y() + 10
