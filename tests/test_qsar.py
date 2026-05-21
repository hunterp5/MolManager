"""Tests for QSAR model fitting (no Qt)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from molmanager.qsar import fit_qsar_model, infer_task_type, predict_qsar_rows


def test_infer_task_type_regression_vs_classification():
    y_reg = np.array([1.2, 3.4, 5.6, 7.8, 2.1, 9.0, 4.4, 6.2])
    assert infer_task_type(y_reg) == "regression"
    y_cls = np.array([0, 0, 1, 1, 0, 1, 1, 0], dtype=float)
    assert infer_task_type(y_cls) == "classification"


def test_fit_and_predict_numeric_regression():
    rng = np.random.default_rng(42)
    n = 40
    mw = rng.uniform(200, 500, size=n)
    logp = rng.uniform(-1, 5, size=n)
    activity = 0.02 * mw + 0.5 * logp + rng.normal(0, 0.5, size=n)
    df = pd.DataFrame({"MW": mw, "LogP": logp, "pIC50": activity})
    oids = list(range(n))
    result = fit_qsar_model(
        df=df,
        oids=oids,
        activity_column="pIC50",
        feature_columns=["MW", "LogP"],
        fp_choice=None,
        mol_rows=None,
        model_key="ridge",
        task_mode="regression",
        train_fraction=0.75,
        cv_folds=3,
        standardize=True,
    )
    assert result.task == "regression"
    assert result.n_train == n
    assert "R²" in result.metrics_text or "RMSE" in result.metrics_text
    preds = predict_qsar_rows(
        result.bundle,
        df=df,
        oids=oids,
        mol_rows=None,
        output_column="QSAR_pIC50",
    )
    assert len(preds) == n
    assert preds[0][1]["QSAR_pIC50"]
