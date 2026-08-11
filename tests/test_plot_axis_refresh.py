# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

"""Tests for plot axis column list helpers."""

import pytest
from PyQt5.QtWidgets import QApplication, QComboBox

from molmanager.ui.plot import AXIS_NONE, PlotWidget, normalize_axis_name


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
