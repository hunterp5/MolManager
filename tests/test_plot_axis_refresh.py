"""Tests for plot axis column list helpers."""

import pytest
from PyQt5.QtWidgets import QApplication, QComboBox

from chemmanager.ui.plot import AXIS_NONE, PlotWidget, normalize_axis_name


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_set_axis_combo_items_preserves_selection(qapp):
    combo = QComboBox()
    PlotWidget._set_axis_combo_items(combo, ["MW", "LogP"], previous="LogP", allow_none=False)
    assert combo.currentText() == "LogP"

    PlotWidget._set_axis_combo_items(combo, ["MW"], previous="LogP", allow_none=False)
    assert combo.currentText() == "MW"


def test_set_axis_combo_items_optional_none(qapp):
    combo = QComboBox()
    PlotWidget._set_axis_combo_items(combo, ["MW", "LogP"], previous=AXIS_NONE, allow_none=True)
    assert normalize_axis_name(combo.currentText()) is None
