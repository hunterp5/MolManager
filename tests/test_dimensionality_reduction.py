"""Tests for PCA / t-SNE / UMAP helpers (no Qt)."""

import numpy as np
import pandas as pd
import pytest

from molmanager.dimensionality_reduction import (
    build_reduction_result,
    is_fingerprint_bitcount_column,
    prepare_numeric_matrix,
    run_pca,
    run_tsne,
    run_umap,
)
from molmanager.ui.dimred_plot import build_dimension_reduction_figure


def test_prepare_numeric_matrix_drops_incomplete_rows():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0, np.nan], "b": [3.0, 4.0, np.nan, 5.0]})
    X, _idx, positions = prepare_numeric_matrix(df, ["a", "b"])
    assert X.shape == (2, 2)
    assert positions == [0, 1]


def test_run_pca_returns_two_components():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, 5))
    coords, ratios, summary = run_pca(X, standardize=True, n_components=2)
    assert coords.shape == (40, 2)
    assert len(ratios) == 2
    assert "PC1" in summary


def test_run_umap_subsample_note():
    pytest.importorskip("umap")
    rng = np.random.default_rng(2)
    X = rng.normal(size=(80, 4))
    coords, used, summary = run_umap(X, max_points=30, random_state=0)
    assert coords.shape == (30, 2)
    assert len(used) == 30
    assert "Subsampled" in summary
    assert "n_neighbors" in summary


def test_run_tsne_subsample_note():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(80, 4))
    coords, used, summary = run_tsne(X, max_points=30, max_iter=300, random_state=0)
    assert coords.shape == (30, 2)
    assert len(used) == 30
    assert "Subsampled" in summary


def test_tsne_single_feature_uses_random_init():
    X = np.random.randint(10, 80, size=(120, 1)).astype(float)
    coords, _used, summary = run_tsne(X, standardize=True, max_iter=400, max_points=100)
    assert coords.shape == (100, 2)
    assert "t-SNE init: random" in summary


def test_fingerprint_bitcount_column_detection():
    assert is_fingerprint_bitcount_column("FP_Morgan_2_1024")
    assert not is_fingerprint_bitcount_column("MolWt")


def test_build_reduction_result_tsne_subsample():
    n = 80
    df = pd.DataFrame({"mw": np.linspace(100.0, 200.0, n)})
    oids = list(range(n))
    rng = np.random.default_rng(1)
    X = rng.normal(size=(n, 4))
    coords, used_idx, _ = run_tsne(X, max_points=30, max_iter=300, random_state=0)
    result = build_reduction_result(
        "tsne", coords, df, oids, used_idx, title="t-SNE", summary="ok"
    )
    assert len(result.oids) == 30


def test_dimred_figure_numeric_string_color_by():
    df = pd.DataFrame({"mw": ["100.5", "200.25", "300.0"]})
    coords = np.array([[0.0, 1.0], [2.0, 3.0], [4.0, 5.0]])
    result = build_reduction_result(
        "pca",
        coords,
        df,
        [1, 2, 3],
        np.arange(3),
        title="PCA",
        summary="ok",
        color_column="mw",
    )
    fig = build_dimension_reduction_figure(result)
    assert fig.data[0].marker.colorscale is not None
    assert all(isinstance(c, float) for c in fig.data[0].marker.color)


def test_build_reduction_result_hover():
    df = pd.DataFrame({"mw": [100.0, 200.0], "cluster": ["A", "B"]})
    coords = np.array([[0.0, 1.0], [2.0, 3.0]])
    result = build_reduction_result(
        "pca",
        coords,
        df,
        [10, 20],
        np.arange(2),
        title="PCA",
        summary="ok",
        color_column="cluster",
    )
    assert result.oids == [10, 20]
    assert len(result.hover) == 2
    assert result.color_values == ["A", "B"]
