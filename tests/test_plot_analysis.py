"""Tests for Plotter statistics and curve fits."""

import numpy as np

from molmanager.plot_analysis import (
    FIT_LINEAR,
    fit_xy_curve,
    summarize_univariate,
    summarize_xy,
)


def test_summarize_xy_includes_correlation():
    lines = summarize_xy([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0], x_label="X", y_label="Y")
    assert any("Pearson" in line for line in lines)


def test_linear_fit_perfect_line():
    x = [0.0, 1.0, 2.0, 3.0]
    y = [1.0, 2.0, 3.0, 4.0]
    result = fit_xy_curve(x, y, FIT_LINEAR)
    assert result is not None
    xs, ys, name = result
    assert len(xs) == 100
    assert abs(ys[0] - 1.0) < 0.05
    assert "Fit:" in name


def test_univariate_summary():
    lines = summarize_univariate([1.0, 2.0, 3.0, 100.0], label="MW")
    assert lines[0].startswith("MW")
    assert any("mean" in line for line in lines)
