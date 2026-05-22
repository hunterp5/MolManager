"""Map table column values to Plotly scatter marker color settings."""

from __future__ import annotations

from typing import Any

import numpy as np

_CATEGORICAL_PALETTE = (
    "#636efa",
    "#ef553b",
    "#00cc96",
    "#ab63fa",
    "#ffa15a",
    "#19d3f3",
    "#ff6692",
    "#b6e880",
    "#ff97ff",
    "#fecb52",
)

_MISSING_TOKENS = frozenset({"", "N/A", "NA", "NAN", "NONE", "NULL"})

DEFAULT_PLOT_COLORSCALE = "Viridis"

# Plotly built-in continuous colorscales (numeric Color by).
PLOT_COLORSCALE_CHOICES: tuple[str, ...] = (
    "Viridis",
    "Plasma",
    "Inferno",
    "Magma",
    "Cividis",
    "Turbo",
    "Blues",
    "Greens",
    "Reds",
    "YlOrRd",
    "RdBu",
    "RdYlBu",
    "Portland",
    "Jet",
)


def resolve_plot_colorscale(name: str | None) -> str:
    """Return a valid Plotly colorscale name."""
    if name and name in PLOT_COLORSCALE_CHOICES:
        return name
    return DEFAULT_PLOT_COLORSCALE


def parse_color_range_bounds(
    min_text: str,
    max_text: str,
) -> tuple[float | None, float | None]:
    """Parse optional min/max edits; empty text means auto (data-driven) bounds."""
    from .utils import safe_float

    lo = safe_float(min_text.strip()) if min_text.strip() else None
    hi = safe_float(max_text.strip()) if max_text.strip() else None
    return lo, hi


def color_values_are_numeric(raw_values: list[Any] | None) -> bool:
    """True when every non-missing value parses as a number (continuous color column)."""
    if not raw_values or not column_values_have_color_data(raw_values):
        return False
    for value in raw_values:
        if _is_missing(value):
            continue
        if _try_float(value) is None:
            return False
    return True


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and np.isnan(value):
        return True
    if isinstance(value, str):
        return value.strip().upper() in _MISSING_TOKENS
    try:
        if np.isnan(float(value)):
            return True
    except (TypeError, ValueError):
        pass
    return False


def column_values_have_color_data(raw_values: list[Any] | None) -> bool:
    """True if at least one value can be used for coloring."""
    if not raw_values:
        return False
    return any(not _is_missing(v) for v in raw_values)


def normalize_color_column(
    color_values: list[Any] | None,
    color_label: str | None,
) -> tuple[list[Any] | None, str | None]:
    """Return ``(None, None)`` when the column has no usable values."""
    if not color_label or color_label == "(none)":
        return None, None
    if not column_values_have_color_data(color_values):
        return None, None
    return color_values, color_label


def _try_float(value: Any) -> float | None:
    if _is_missing(value):
        return float("nan")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if hasattr(value, "item"):
        try:
            return float(value.item())
        except (TypeError, ValueError):
            pass
    text = str(value).strip().replace(",", "")
    if not text or text.upper() in _MISSING_TOKENS:
        return float("nan")
    try:
        return float(text)
    except ValueError:
        return None


def scatter_marker_from_column_values(
    raw_values: list[Any] | None,
    *,
    color_label: str | None = None,
    colorscale: str | None = None,
    color_min: float | None = None,
    color_max: float | None = None,
    default_color: str = "#2a74d6",
    point_size: float = 6,
    opacity: float = 0.85,
) -> dict:
    """
    Build a Plotly ``marker`` dict for scatter traces from per-point column values.

    Numeric columns (including numeric strings from the table) use a continuous colorscale.
    Text/category columns use a fixed discrete palette.
    """
    if not raw_values or not column_values_have_color_data(raw_values):
        return {"size": point_size, "opacity": opacity, "color": default_color}

    numeric: list[float] = []
    for value in raw_values:
        parsed = _try_float(value)
        if parsed is None:
            numeric.clear()
            break
        numeric.append(parsed)

    if numeric:
        plot_colors = [float(v) if v == v else float("nan") for v in numeric]
        finite = [v for v in plot_colors if v == v]
        if not finite:
            return {"size": point_size, "opacity": opacity, "color": default_color}
        lo, hi = min(finite), max(finite)
        if color_min is not None:
            lo = float(color_min)
        if color_max is not None:
            hi = float(color_max)
        if lo > hi:
            lo, hi = hi, lo
        if abs(hi - lo) < 1e-12:
            lo -= 0.5
            hi += 0.5
        marker: dict = {
            "size": point_size,
            "opacity": opacity,
            "color": plot_colors,
            "colorscale": resolve_plot_colorscale(colorscale),
            "showscale": True,
            "cmin": lo,
            "cmax": hi,
        }
        if color_label:
            marker["colorbar"] = {"title": color_label}
        return marker

    categories: list[str] = []
    for value in raw_values:
        if _is_missing(value):
            categories.append("(missing)")
        else:
            text = str(value).strip()
            categories.append(text if text else "(missing)")
    uniq = sorted(set(categories), key=lambda x: (x == "(missing)", x.lower()))
    color_map = {cat: _CATEGORICAL_PALETTE[i % len(_CATEGORICAL_PALETTE)] for i, cat in enumerate(uniq)}
    point_colors = [color_map[c] for c in categories]
    return {
        "size": point_size,
        "opacity": opacity,
        "color": point_colors,
    }
