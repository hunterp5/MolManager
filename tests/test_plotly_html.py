"""Tests for embedded Plotly HTML export."""

import json
from pathlib import Path

from plotly import graph_objects as go

from molmanager.plot_color import scatter_marker_from_column_values
from molmanager.ui.plotly_html import figure_payload_json, write_self_contained_plotly_html


def test_figure_payload_json_parses_with_nan_marker_colors():
    marker = scatter_marker_from_column_values([None, 1.0, 2.0], color_label="MW")
    fig = go.Figure(data=[go.Scatter(x=[1, 2, 3], y=[1, 2, 3], marker=marker)])
    raw = figure_payload_json(fig)
    assert "NaN" not in raw
    payload = json.loads(raw)
    assert payload["data"][0]["marker"]["color"]


def test_write_self_contained_plotly_html_includes_plotly(tmp_path: Path):
    fig = go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 4])])
    path = tmp_path / "plot.html"
    write_self_contained_plotly_html(fig, path)
    text = path.read_text(encoding="utf-8")
    assert "Plotly.newPlot" in text
    assert 'src="https://cdn.plot.ly' not in text
