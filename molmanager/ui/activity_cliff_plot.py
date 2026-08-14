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

"""Plotly figure for the Activity Cliff Map tool."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import md5

from plotly import graph_objects as go

from ..activity_cliff_analysis import ActivityCliffPoint
from .plotly_html import finalize_plot_legend


def _x_jitter(oid_a: int, oid_b: int, scale: float = 0.12) -> float:
    """Tiny deterministic jitter so integer HA counts do not fully overlap."""
    digest = md5(f"{oid_a}:{oid_b}".encode("utf-8")).hexdigest()
    unit = (int(digest[:8], 16) / 0xFFFFFFFF) - 0.5
    return float(unit) * scale


def build_activity_cliff_figure(
    points: Sequence[ActivityCliffPoint],
    *,
    activity_column: str,
    x_mode: str = "heavy_atoms",
) -> go.Figure:
    """
    Scatter of structural-change size vs ``|Δactivity|``, colored by signed Δ.

    ``x_mode`` is ``\"heavy_atoms\"`` (default) or ``\"frag_distance\"``.
    """
    use_distance = (x_mode or "").strip().lower() in {"frag_distance", "distance", "tanimoto"}
    xs: list[float] = []
    ys: list[float] = []
    colors: list[float] = []
    custom: list[list[int]] = []
    for p in points:
        if use_distance:
            x = float(p.frag_distance)
            x_label = "Fragment distance (1 − Tanimoto)"
        else:
            x = float(p.change_heavy_atoms) + _x_jitter(p.oid_a, p.oid_b)
            x_label = "Changing heavy atoms"
        xs.append(x)
        ys.append(float(p.abs_delta))
        colors.append(float(p.signed_delta))
        custom.append([int(p.oid_a), int(p.oid_b), int(p.pair_index)])

    cmax = max((abs(c) for c in colors), default=1.0) or 1.0
    fig = go.Figure(
        data=[
            go.Scatter(
                x=xs,
                y=ys,
                mode="markers",
                customdata=custom,
                hoverinfo="none",
                marker={
                    "size": 9,
                    "color": colors,
                    "colorscale": "RdBu",
                    "cmin": -cmax,
                    "cmax": cmax,
                    "colorbar": {"title": f"Δ{activity_column}"},
                    "line": {"width": 0.5, "color": "#333"},
                    "opacity": 0.85,
                },
                showlegend=False,
                unselected={"marker": {"opacity": 0.25}},
                selected={"marker": {"size": 11, "opacity": 1.0}},
            )
        ]
    )
    fig.update_layout(
        title="Activity Cliff Map",
        xaxis_title=x_label,
        yaxis_title=f"|Δ{activity_column}|",
        template="plotly_white",
        dragmode="lasso",
        clickmode="event+select",
        showlegend=False,
        margin=dict(l=56, r=24, t=48, b=56),
        meta={
            "molmanager_selection_traces": [0],
            "molmanager_hover_persist": True,
        },
    )
    finalize_plot_legend(fig)
    return fig
