"""Statistics summaries and curve fits for Plotly charts (Plotter)."""

from __future__ import annotations

import numpy as np

FIT_NONE = "none"
FIT_LINEAR = "linear"
FIT_QUADRATIC = "quadratic"

PLOT_FIT_CHOICES: tuple[tuple[str, str], ...] = (
    ("None", FIT_NONE),
    ("Linear fit", FIT_LINEAR),
    ("Quadratic fit", FIT_QUADRATIC),
)


def _finite_array(values: list[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    mask = np.isfinite(arr)
    return arr[mask]


def _paired_finite(x: list[float], y: list[float]) -> tuple[np.ndarray, np.ndarray]:
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    n = min(xa.size, ya.size)
    xa = xa[:n]
    ya = ya[:n]
    mask = np.isfinite(xa) & np.isfinite(ya)
    return xa[mask], ya[mask]


def summarize_univariate(values: list[float], *, label: str) -> list[str]:
    """Summary lines for one numeric series (histogram, box, violin)."""
    arr = _finite_array(values)
    n = int(arr.size)
    if n == 0:
        return [f"{label}: no data"]
    lines = [
        f"{label} (n={n})",
        f"  mean = {_fmt(arr.mean())}",
        f"  median = {_fmt(np.median(arr))}",
        f"  std = {_fmt(arr.std(ddof=1)) if n > 1 else '—'}",
        f"  min = {_fmt(arr.min())}",
        f"  max = {_fmt(arr.max())}",
    ]
    if n >= 4:
        q1, q3 = np.percentile(arr, [25, 75])
        lines.append(f"  Q1 = {_fmt(q1)}  Q3 = {_fmt(q3)}")
    return lines


def summarize_xy(
    x: list[float],
    y: list[float],
    *,
    x_label: str,
    y_label: str,
) -> list[str]:
    """Summary lines for paired X/Y data (2D scatter or line)."""
    xa, ya = _paired_finite(x, y)
    n = int(xa.size)
    if n == 0:
        return ["No data"]
    lines = [
        f"n = {n}",
        f"{x_label}: mean {_fmt(xa.mean())}  σ {_fmt(xa.std(ddof=1)) if n > 1 else '—'}",
        f"{y_label}: mean {_fmt(ya.mean())}  σ {_fmt(ya.std(ddof=1)) if n > 1 else '—'}",
    ]
    if n >= 2:
        r = float(np.corrcoef(xa, ya)[0, 1])
        if np.isfinite(r):
            lines.append(f"Pearson r = {r:.4f}")
    return lines


def fit_xy_curve(
    x: list[float],
    y: list[float],
    fit_kind: str,
    *,
    n_points: int = 100,
) -> tuple[list[float], list[float], str] | None:
    """
    Return fitted (x_line, y_line, legend name) for overlay on a 2D plot.

    ``fit_kind`` is ``FIT_LINEAR``, ``FIT_QUADRATIC``, or ``FIT_NONE``.
    """
    if fit_kind == FIT_NONE:
        return None
    xa, ya = _paired_finite(x, y)
    n = int(xa.size)
    if n < 2:
        return None
    order = 1 if fit_kind == FIT_LINEAR else 2
    if fit_kind == FIT_QUADRATIC and n < 3:
        return None
    try:
        coeffs = np.polyfit(xa, ya, order)
    except np.linalg.LinAlgError:
        return None
    x0, x1 = float(xa.min()), float(xa.max())
    if abs(x1 - x0) < 1e-12:
        return None
    xs = np.linspace(x0, x1, max(2, int(n_points)))
    ys = np.polyval(coeffs, xs)
    if order == 1:
        name = f"Fit: y = {coeffs[0]:.4g}x + {coeffs[1]:.4g}"
    else:
        name = f"Fit: y = {coeffs[0]:.4g}x² + {coeffs[1]:.4g}x + {coeffs[2]:.4g}"
    return xs.tolist(), ys.tolist(), name


def _fmt(value: float) -> str:
    av = abs(float(value))
    if av >= 1000 or (av > 0 and av < 0.001):
        return f"{value:.4g}"
    if av >= 100:
        return f"{value:.2f}"
    return f"{value:.4f}"
