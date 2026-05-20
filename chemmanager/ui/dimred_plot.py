"""Build Plotly figures for PCA / t-SNE / UMAP result dialogs."""

from __future__ import annotations

from plotly import graph_objects as go

from ..dimensionality_reduction import DimensionReductionResult


def build_dimension_reduction_figure(result: DimensionReductionResult) -> go.Figure:
    if result.method == "pca":
        x_label, y_label = "PC1", "PC2"
    elif result.method == "umap":
        x_label, y_label = "UMAP 1", "UMAP 2"
    else:
        x_label, y_label = "t-SNE 1", "t-SNE 2"
    marker: dict = {"size": 6, "opacity": 0.85, "color": "#2a74d6"}
    if result.color_values is not None:
        marker = {
            "size": 6,
            "opacity": 0.85,
            "color": result.color_values,
            "colorscale": "Viridis",
            "showscale": True,
        }
        if result.color_label:
            marker["colorbar"] = {"title": result.color_label}
    fig = go.Figure(
        data=[
            go.Scatter(
                x=result.x,
                y=result.y,
                mode="markers",
                text=result.hover,
                hoverinfo="text",
                marker=marker,
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
        margin=dict(l=48, r=24, t=48, b=48),
    )
    return fig
