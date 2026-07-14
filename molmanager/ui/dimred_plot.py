"""Build Plotly figures for PCA / t-SNE / UMAP / SOM result dialogs."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from plotly import graph_objects as go

from ..dimensionality_reduction import DimensionReductionResult
from ..plot_color import DEFAULT_PLOT_COLORSCALE, scatter_marker_from_column_values
from ..ui.plotly_html import finalize_plot_legend


def dimension_reduction_result_with_color(
    result: DimensionReductionResult,
    *,
    color_values: list[Any] | None,
    color_label: str | None,
) -> DimensionReductionResult:
    """Return a copy of ``result`` with updated color encoding and hover text."""
    hover: list[str] = []
    for i, oid in enumerate(result.oids):
        parts = [f"OID {oid}"]
        if color_label and color_values is not None and i < len(color_values):
            parts.append(f"{color_label}: {color_values[i]}")
        hover.append("<br>".join(parts))
    return replace(
        result,
        color_values=color_values,
        color_label=color_label,
        hover=hover,
    )


def build_dimension_reduction_figure(
    result: DimensionReductionResult,
    *,
    colorscale: str = DEFAULT_PLOT_COLORSCALE,
    color_min: float | None = None,
    color_max: float | None = None,
) -> go.Figure:
    if result.method == "pca":
        x_label, y_label = "PC1", "PC2"
    elif result.method == "umap":
        x_label, y_label = "UMAP 1", "UMAP 2"
    elif result.method == "som":
        x_label, y_label = "SOM column", "SOM row"
    else:
        x_label, y_label = "t-SNE 1", "t-SNE 2"
    marker = scatter_marker_from_column_values(
        result.color_values,
        color_label=result.color_label,
        colorscale=colorscale,
        color_min=color_min,
        color_max=color_max,
    )
    fig = go.Figure(
        data=[
            go.Scatter(
                x=result.x,
                y=result.y,
                mode="markers",
                text=result.hover,
                hoverinfo="text",
                marker=marker,
                showlegend=False,
                unselected={"marker": {"opacity": 0.35}},
                selected={"marker": {"size": 9, "color": "#d62828", "opacity": 1.0}},
            )
        ]
    )
    fig.update_layout(
        title=result.title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        template="plotly_white",
        dragmode="lasso",
        showlegend=False,
        margin=dict(l=48, r=24, t=48, b=48),
    )
    return finalize_plot_legend(fig)
