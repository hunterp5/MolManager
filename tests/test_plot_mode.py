"""Tests for plot type inference from axis selections."""

from molmanager.ui.plot import (
    AXIS_NONE,
    PLOT_TYPE_LINE_2D,
    PLOT_TYPE_SCATTER,
    infer_plot_mode,
    normalize_axis_name,
    resolve_plot_mode,
)


def test_normalize_axis_name():
    assert normalize_axis_name(None) is None
    assert normalize_axis_name("") is None
    assert normalize_axis_name(AXIS_NONE) is None
    assert normalize_axis_name("  MW  ") == "MW"


def test_infer_plot_mode_histogram():
    assert infer_plot_mode("MW", AXIS_NONE, AXIS_NONE) == "Histogram"
    assert infer_plot_mode("MW", None, None) == "Histogram"


def test_infer_plot_mode_2d():
    assert infer_plot_mode("MW", "LogP", AXIS_NONE) == "2D"
    assert infer_plot_mode("MW", "LogP", None) == "2D"


def test_infer_plot_mode_3d():
    assert infer_plot_mode("MW", "LogP", "TPSA") == "3D"


def test_infer_plot_mode_invalid():
    assert infer_plot_mode(None, "LogP", None) is None
    assert infer_plot_mode("MW", AXIS_NONE, "TPSA") is None
    assert infer_plot_mode(AXIS_NONE, "LogP", "TPSA") is None


def test_resolve_plot_mode_scatter_matches_infer():
    axes = ("MW", "LogP", AXIS_NONE)
    assert resolve_plot_mode(PLOT_TYPE_SCATTER, *axes) == infer_plot_mode(*axes)


def test_resolve_plot_mode_line_2d():
    assert resolve_plot_mode(PLOT_TYPE_LINE_2D, "MW", "LogP", AXIS_NONE) == "2D"
    assert resolve_plot_mode(PLOT_TYPE_LINE_2D, "MW", AXIS_NONE, AXIS_NONE) is None
