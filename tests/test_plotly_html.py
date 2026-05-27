"""Tests for embedded Plotly HTML export."""

import json
from pathlib import Path

from plotly import graph_objects as go

from molmanager.plot_color import scatter_marker_from_column_values
from molmanager.ui.plotly_html import (
    figure_payload_json,
    legend_name_is_utility,
    suppress_utility_legend_entries,
    write_self_contained_plotly_html,
)


def test_figure_payload_json_parses_with_nan_marker_colors():
    marker = scatter_marker_from_column_values([None, 1.0, 2.0], color_label="MW")
    fig = go.Figure(data=[go.Scatter(x=[1, 2, 3], y=[1, 2, 3], marker=marker)])
    raw = figure_payload_json(fig)
    assert "NaN" not in raw
    payload = json.loads(raw)
    assert payload["data"][0]["marker"]["color"]


def test_suppress_utility_legend_entries_hides_fit_and_trace_zero() -> None:
    fig = go.Figure(
        data=[
            go.Scatter(x=[1], y=[1], name="Fit"),
            go.Histogram(x=[1, 2, 2], name="Trace 0"),
            go.Scatter(x=[2], y=[2], name="OID 42"),
        ]
    )
    suppress_utility_legend_entries(fig)
    assert fig.data[0].showlegend is False
    assert fig.data[1].showlegend is False
    assert fig.data[2].showlegend is not False
    assert fig.layout.showlegend is not False


def test_legend_name_is_utility_compounds_and_fit_prefix() -> None:
    assert legend_name_is_utility("Compounds")
    assert legend_name_is_utility("Fit (normal)")
    assert legend_name_is_utility("") is True
    assert legend_name_is_utility("OID 3") is False


def test_suppress_utility_legend_entries_hides_compounds() -> None:
    fig = go.Figure(data=[go.Scatter(x=[1], y=[1], name="Compounds")])
    suppress_utility_legend_entries(fig)
    assert fig.data[0].showlegend is False
    assert fig.layout.showlegend is False


def test_figure_payload_json_hides_utility_legend() -> None:
    fig = go.Figure(data=[go.Scatter(x=[1], y=[1], name="Fit")])
    payload = json.loads(figure_payload_json(fig))
    assert payload["data"][0]["showlegend"] is False
    assert payload["layout"].get("showlegend") is False


def test_write_self_contained_plotly_html_includes_plotly(tmp_path: Path):
    fig = go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 4])])
    path = tmp_path / "plot.html"
    write_self_contained_plotly_html(fig, path)
    text = path.read_text(encoding="utf-8")
    assert "Plotly.newPlot" in text
    assert 'src="https://cdn.plot.ly' not in text
