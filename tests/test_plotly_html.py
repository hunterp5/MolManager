"""Tests for embedded Plotly HTML export."""

from pathlib import Path

from plotly import graph_objects as go

from molmanager.ui.plotly_html import write_self_contained_plotly_html


def test_write_self_contained_plotly_html_includes_plotly(tmp_path: Path):
    fig = go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 4])])
    path = tmp_path / "plot.html"
    write_self_contained_plotly_html(fig, path)
    text = path.read_text(encoding="utf-8")
    assert "Plotly.newPlot" in text
    assert 'src="https://cdn.plot.ly' not in text
