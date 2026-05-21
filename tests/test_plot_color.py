"""Tests for Plotly scatter color encoding."""

from molmanager.plot_color import (
    scatter_marker_from_column_values,
)


def test_numeric_strings_use_colorscale_not_hex():
    m = scatter_marker_from_column_values(
        ["457.6814032", "539.62482", "384.2372"],
        color_label="MW",
    )
    assert m["colorscale"] == "Viridis"
    assert all(isinstance(c, float) for c in m["color"])


def test_categorical_uses_hex_colors():
    m = scatter_marker_from_column_values(["A", "B", "A"], color_label="cluster")
    assert "colorscale" not in m
    assert m["color"][0].startswith("#")


def test_native_floats_use_colorscale():
    m = scatter_marker_from_column_values([1.0, 2.0, 3.0], color_label="score")
    assert m["colorscale"] == "Viridis"
    assert m["cmin"] == 1.0
    assert m["cmax"] == 3.0


def test_custom_colorscale():
    m = scatter_marker_from_column_values([1.0, 2.0], colorscale="Plasma")
    assert m["colorscale"] == "Plasma"


def test_all_none_uses_default_marker():
    m = scatter_marker_from_column_values([None, None, None], color_label="MW")
    assert m["color"] == "#2a74d6"
    assert "colorscale" not in m


def test_mixed_none_and_float_uses_nan_for_missing():
    import math

    from plotly import graph_objects as go

    m = scatter_marker_from_column_values([None, None, 1.0], color_label="MW")
    assert m["colorscale"] == "Viridis"
    assert math.isnan(m["color"][0])
    assert math.isnan(m["color"][1])
    assert m["color"][2] == 1.0
    go.Figure(data=[go.Scatter(x=[1, 2, 3], y=[1, 2, 3], marker=m)])
