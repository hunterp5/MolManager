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

"""Tests for docked-plot panel width helpers."""

from molmanager.ui.dockable_plot import (
    PLOT_PANEL_BASE_MINIMUM_WIDTH,
    PLOT_PANEL_DEFAULT_WIDTH,
    plot_embedded_minimum_width,
    plot_embedded_preferred_width,
)


class _WidePlot:
    def embedded_minimum_width(self) -> int:
        return 700

    def embedded_preferred_width(self) -> int:
        return 900


def test_plot_embedded_widths_use_custom_hooks():
    w = _WidePlot()
    assert plot_embedded_minimum_width(w) == 700
    assert plot_embedded_preferred_width(w) == 900


def test_plot_embedded_widths_fallback_without_widget():
    assert plot_embedded_minimum_width(None) == PLOT_PANEL_BASE_MINIMUM_WIDTH
    assert plot_embedded_preferred_width(None) == max(
        PLOT_PANEL_BASE_MINIMUM_WIDTH, PLOT_PANEL_DEFAULT_WIDTH
    )


def test_plot_widget_embedded_minimum_covers_axes_and_stats():
    from molmanager.ui.plot import PlotWidget

    expected_min = (
        PlotWidget._AXES_CONTROLS_MIN_WIDTH
        + PlotWidget._STATS_PANEL_MIN_WIDTH
        + 6
        + 8
    )
    stub = type(
        "W",
        (),
        {
            "embedded_minimum_width": lambda self: expected_min,
            "embedded_preferred_width": lambda self: max(expected_min, 840),
        },
    )()
    assert plot_embedded_minimum_width(stub) == expected_min
    assert plot_embedded_preferred_width(stub) >= expected_min
    assert expected_min >= PlotWidget._AXES_CONTROLS_MIN_WIDTH + PlotWidget._STATS_PANEL_MIN_WIDTH
