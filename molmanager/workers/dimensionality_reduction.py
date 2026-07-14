"""Background PCA / t-SNE / UMAP / SOM jobs."""

from __future__ import annotations

import threading
from dataclasses import asdict

import numpy as np
import pandas as pd
from PyQt5.QtCore import QObject, QRunnable, pyqtSignal

from ..dimensionality_reduction import (
    DimensionReductionResult,
    build_reduction_result,
    run_pca,
    run_som,
    run_tsne,
    run_umap,
)
from ..feature_matrix import build_combined_feature_matrix, standardize_feature_matrix


class DimensionReductionSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class DimensionReductionWorker(QRunnable):
    def __init__(
        self,
        params: dict,
        signals: DimensionReductionSignals,
        cancel_event: threading.Event | None = None,
    ):
        super().__init__()
        self.params = dict(params)
        self.signals = signals
        self.cancel_event = cancel_event

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def run(self) -> None:
        if self._cancelled():
            self.signals.failed.emit("Cancelled.")
            return
        try:
            result = _compute(self.params)
            if self._cancelled():
                self.signals.failed.emit("Cancelled.")
                return
            self.signals.finished.emit(result)
        except Exception as exc:
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)


def _compute(params: dict) -> DimensionReductionResult:
    method = str(params.get("method") or "").lower()
    standardize = bool(params.get("standardize", True))
    color_column = params.get("color_column") or None
    df: pd.DataFrame = params["dataframe"]
    oids: list[int] = list(params["oids"])
    feature_columns = list(params.get("feature_columns") or [])
    fp_choice = str(params.get("fingerprint") or "").strip() or None
    mol_rows = list(params.get("mol_rows") or []) if params.get("use_fingerprints") else None

    built = build_combined_feature_matrix(
        df=df,
        oids=oids,
        feature_columns=feature_columns or None,
        mol_rows=mol_rows,
        fp_choice=fp_choice,
        min_rows=2,
    )
    X, _ = standardize_feature_matrix(
        built.X,
        built.n_numeric_features,
        enabled=standardize,
        fit=True,
    )
    oids_sub = built.oids
    df_sub = df.iloc[built.df_positions].reset_index(drop=True)
    fp_note = built.summary + "\n\n"

    if method == "pca":
        n_components = int(params.get("n_components", 2))
        coords, _ratios, summary = run_pca(X, standardize=standardize, n_components=n_components)
        title = "Principal Component Analysis"
        row_ix = np.arange(coords.shape[0])
        return build_reduction_result(
            "pca",
            coords,
            df_sub,
            oids_sub,
            row_ix,
            title=title,
            summary=fp_note + summary,
            color_column=color_column,
        )
    if method == "tsne":
        coords, used_idx, summary = run_tsne(
            X,
            standardize=standardize,
            perplexity=float(params.get("perplexity", 30.0)),
            learning_rate=float(params.get("learning_rate", 200.0)),
            max_iter=int(params.get("max_iter", 1000)),
            random_state=int(params.get("random_state", 42)),
            max_points=params.get("max_points"),
        )
        title = "t-SNE Visualization"
        return build_reduction_result(
            "tsne",
            coords,
            df_sub,
            oids_sub,
            used_idx,
            title=title,
            summary=fp_note + summary,
            color_column=color_column,
        )
    if method == "umap":
        coords, used_idx, summary = run_umap(
            X,
            standardize=standardize,
            n_neighbors=int(params.get("n_neighbors", 15)),
            min_dist=float(params.get("min_dist", 0.1)),
            random_state=int(params.get("random_state", 42)),
            max_points=params.get("max_points"),
        )
        title = "UMAP Visualization"
        return build_reduction_result(
            "umap",
            coords,
            df_sub,
            oids_sub,
            used_idx,
            title=title,
            summary=fp_note + summary,
            color_column=color_column,
        )
    if method == "som":
        sigma_raw = params.get("sigma")
        sigma = None if sigma_raw is None or float(sigma_raw) <= 0 else float(sigma_raw)
        coords, used_idx, summary = run_som(
            X,
            standardize=standardize,
            grid_width=int(params.get("grid_width", 10)),
            grid_height=int(params.get("grid_height", 10)),
            n_epochs=int(params.get("n_epochs", 50)),
            learning_rate=float(params.get("learning_rate", 0.5)),
            sigma=sigma,
            random_state=int(params.get("random_state", 42)),
            max_points=params.get("max_points"),
            jitter=float(params.get("jitter", 0.35)),
        )
        title = "Self-Organizing Map"
        return build_reduction_result(
            "som",
            coords,
            df_sub,
            oids_sub,
            used_idx,
            title=title,
            summary=fp_note + summary,
            color_column=color_column,
        )
    raise ValueError(f"Unknown method: {method!r}")


def _dataframe_for_oids(df: pd.DataFrame, oids: list[int]) -> pd.DataFrame:
    """Align color/metadata rows to fingerprint oids in result order."""
    if not oids:
        return df.iloc[0:0].copy()
    if "ID_HIDDEN" in df.columns:
        id_series = pd.to_numeric(df["ID_HIDDEN"], errors="coerce")
        rows: list[pd.Series] = []
        for oid in oids:
            match = df.loc[id_series == oid]
            if len(match):
                rows.append(match.iloc[0])
        if rows:
            return pd.DataFrame(rows).reset_index(drop=True)
    if len(df) == len(oids):
        return df.reset_index(drop=True)
    return pd.DataFrame(index=range(len(oids)))


def result_to_dict(result: DimensionReductionResult) -> dict:
    return asdict(result)
