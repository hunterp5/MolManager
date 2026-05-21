"""Plotly figures for BOILED-Egg and golden-triangle medicinal chemistry plots."""

from __future__ import annotations

from typing import Any

from plotly import graph_objects as go

from ..medchem_space import (
    MedChemSpaceDataset,
    bbb_polygon,
    gia_polygon,
    golden_triangle_polygon,
)
from ..plot_color import DEFAULT_PLOT_COLORSCALE


def _path_shape(
    polygon: list[tuple[float, float]],
    *,
    fillcolor: str,
    line_color: str,
) -> dict:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    path = "M " + " L ".join(f"{x},{y}" for x, y in zip(xs, ys, strict=True)) + " Z"
    return dict(
        type="path",
        path=path,
        fillcolor=fillcolor,
        line=dict(color=line_color, width=1.5),
        xref="x",
        yref="y",
        layer="below",
    )


def _scatter_marker(
    color_values: list[Any] | None,
    color_label: str | None,
    *,
    colorscale: str = DEFAULT_PLOT_COLORSCALE,
) -> dict:
    from ..plot_color import scatter_marker_from_column_values

    return scatter_marker_from_column_values(
        color_values,
        color_label=color_label,
        colorscale=colorscale,
        point_size=7,
        opacity=0.88,
    )


def _compound_scatter(
    xs: list[float],
    ys: list[float],
    hover: list[str],
    marker: dict,
) -> go.Scatter:
    return go.Scatter(
        x=xs,
        y=ys,
        mode="markers",
        text=hover,
        hoverinfo="text",
        marker=marker,
        name="Compounds",
        unselected={"marker": {"opacity": 0.35}},
        selected={"marker": {"size": 10, "color": "#d62828", "opacity": 1.0}},
    )


def build_boiled_egg_figure(
    dataset: MedChemSpaceDataset,
    *,
    color_values: list[Any] | None = None,
    color_label: str | None = None,
    colorscale: str = DEFAULT_PLOT_COLORSCALE,
) -> go.Figure:
    """TPSA vs LogP with GIA (white) and BBB (yellow) regions."""
    pts = dataset.points
    marker = _scatter_marker(color_values, color_label, colorscale=colorscale)
    fig = go.Figure(
        data=[
            _compound_scatter(
                [p.tpsa for p in pts],
                [p.wlogp for p in pts],
                [p.hover for p in pts],
                marker,
            )
        ]
    )
    fig.update_layout(
        template="plotly_white",
        dragmode="lasso",
        margin=dict(l=56, r=24, t=24, b=48),
        shapes=[
            dict(
                type="rect",
                xref="x",
                yref="y",
                x0=-20,
                x1=220,
                y0=-3,
                y1=8,
                fillcolor="rgba(235,235,235,0.55)",
                line=dict(width=0),
                layer="below",
            ),
            _path_shape(
                gia_polygon(),
                fillcolor="rgba(255,255,255,0.92)",
                line_color="rgba(40,40,40,0.9)",
            ),
            _path_shape(
                bbb_polygon(),
                fillcolor="rgba(255,220,40,0.75)",
                line_color="rgba(180,140,0,0.9)",
            ),
        ],
    )
    fig.update_xaxes(title_text="TPSA (Ų)", range=[-20, 220])
    fig.update_yaxes(title_text="LogP", range=[-3, 8])
    return fig


def build_golden_triangle_figure(
    dataset: MedChemSpaceDataset,
    *,
    color_values: list[Any] | None = None,
    color_label: str | None = None,
    colorscale: str = DEFAULT_PLOT_COLORSCALE,
) -> go.Figure:
    """MW vs LogP with the golden-triangle drug-likeness region."""
    pts = dataset.points
    marker = _scatter_marker(color_values, color_label, colorscale=colorscale)
    fig = go.Figure(
        data=[
            _compound_scatter(
                [p.logp for p in pts],
                [p.mw for p in pts],
                [p.hover for p in pts],
                marker,
            )
        ]
    )
    fig.update_layout(
        template="plotly_white",
        dragmode="lasso",
        margin=dict(l=56, r=24, t=24, b=48),
        shapes=[
            _path_shape(
                golden_triangle_polygon(),
                fillcolor="rgba(218,165,32,0.35)",
                line_color="rgba(160,120,20,0.95)",
            ),
        ],
    )
    fig.update_xaxes(title_text="LogP", range=[-3, 6])
    fig.update_yaxes(title_text="Molecular weight (Da)", range=[150, 520])
    return fig
