"""QSAR model fitting and prediction (scikit-learn; no Qt)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

TaskKind = Literal["regression", "classification"]

REGRESSION_MODELS: dict[str, str] = {
    "ridge": "Ridge regression",
    "random_forest": "Random forest",
    "gradient_boosting": "Gradient boosting",
}
CLASSIFICATION_MODELS: dict[str, str] = {
    "logistic": "Logistic regression",
    "random_forest": "Random forest",
    "gradient_boosting": "Gradient boosting",
    "svm": "Linear SVM",
}


@dataclass(frozen=True)
class QSARModelBundle:
    """Fitted model and preprocessing state for predictions on new rows."""

    task: TaskKind
    model_key: str
    activity_column: str
    feature_columns: tuple[str, ...] | None
    fp_choice: str | None
    n_numeric_features: int
    standardize_numeric: bool
    model: Any
    scaler: Any | None
    class_labels: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class QSARFitResult:
    task: TaskKind
    model_key: str
    activity_column: str
    metrics_text: str
    bundle: QSARModelBundle
    n_train: int
    n_features: int
    top_features_text: str = ""


def infer_task_type(y: np.ndarray, *, force: TaskKind | None = None) -> TaskKind:
    """Choose regression vs classification from activity values."""
    if force is not None:
        return force
    y = np.asarray(y, dtype=float)
    finite = y[np.isfinite(y)]
    if finite.size < 3:
        return "regression"
    rounded = np.round(finite, 6)
    uniq = np.unique(rounded)
    if len(uniq) <= 2:
        return "classification"
    if len(uniq) <= 12 and np.allclose(uniq, np.round(uniq)):
        if len(uniq) / max(finite.size, 1) < 0.35:
            return "classification"
    return "regression"


def _activity_and_features(
    *,
    df: pd.DataFrame,
    oids: list[int],
    activity_column: str,
    feature_columns: list[str] | None,
    mol_rows: list[tuple[int, object]] | None,
    fp_choice: str | None,
    min_rows: int = 8,
) -> tuple[np.ndarray, np.ndarray, list[str] | None, int, str]:
    """Build X and y for QSAR from numeric columns, fingerprints, or both."""
    from .feature_matrix import build_combined_feature_matrix

    if activity_column not in df.columns:
        raise ValueError(f"Activity column not found: {activity_column}")
    built = build_combined_feature_matrix(
        df=df,
        oids=oids,
        feature_columns=feature_columns,
        mol_rows=mol_rows,
        fp_choice=fp_choice,
        min_rows=min_rows,
        activity_column=activity_column,
    )
    y_all = pd.to_numeric(df[activity_column], errors="coerce").to_numpy(dtype=float)
    y = np.asarray([float(y_all[p]) for p in built.df_positions], dtype=float)
    feat_names = built.feature_names if built.n_numeric_features > 0 else None
    return built.X, y, feat_names, built.n_numeric_features, built.summary


def _prepare_labels(y: np.ndarray, task: TaskKind) -> tuple[np.ndarray, tuple[Any, ...] | None]:
    if task == "regression":
        return y.astype(float), None
    rounded = np.round(y, 6)
    labels = tuple(sorted(np.unique(rounded).tolist()))
    label_to_i = {lab: i for i, lab in enumerate(labels)}
    y_cls = np.asarray([label_to_i[v] for v in rounded], dtype=int)
    return y_cls, labels


def _make_model(task: TaskKind, model_key: str) -> Any:
    if task == "regression":
        if model_key == "ridge":
            from sklearn.linear_model import Ridge

            return Ridge(alpha=1.0)
        if model_key == "random_forest":
            from sklearn.ensemble import RandomForestRegressor

            return RandomForestRegressor(n_estimators=200, random_state=42, n_jobs=-1)
        if model_key == "gradient_boosting":
            from sklearn.ensemble import GradientBoostingRegressor

            return GradientBoostingRegressor(random_state=42)
        raise ValueError(f"Unknown regression model: {model_key}")
    if model_key == "logistic":
        from sklearn.linear_model import LogisticRegression

        return LogisticRegression(max_iter=3000, random_state=42)
    if model_key == "random_forest":
        from sklearn.ensemble import RandomForestClassifier

        return RandomForestClassifier(n_estimators=200, random_state=42, n_jobs=-1)
    if model_key == "gradient_boosting":
        from sklearn.ensemble import GradientBoostingClassifier

        return GradientBoostingClassifier(random_state=42)
    if model_key == "svm":
        from sklearn.svm import SVC

        return SVC(kernel="linear", random_state=42)
    raise ValueError(f"Unknown classification model: {model_key}")


def _scale_fit(X: np.ndarray, *, standardize_numeric: bool, n_numeric: int) -> tuple[np.ndarray, Any | None]:
    from .feature_matrix import standardize_feature_matrix

    return standardize_feature_matrix(
        X, n_numeric, enabled=standardize_numeric, fit=True
    )


def _scale_apply(X: np.ndarray, scaler: Any | None, *, n_numeric: int) -> np.ndarray:
    from .feature_matrix import standardize_feature_matrix

    out, _ = standardize_feature_matrix(
        X, n_numeric, enabled=scaler is not None, scaler_state=scaler, fit=False
    )
    return out


def _regression_metrics(y: np.ndarray, y_hat: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=float)
    y_hat = np.asarray(y_hat, dtype=float)
    n = len(y)
    ss_res = float(np.sum((y - y_hat) ** 2))
    y_mean = float(np.mean(y))
    ss_tot = float(np.sum((y - y_mean) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    rmse = float(np.sqrt(ss_res / n)) if n else float("nan")
    mae = float(np.mean(np.abs(y - y_hat))) if n else float("nan")
    return {"r2": r2, "rmse": rmse, "mae": mae}


def _classification_metrics(y: np.ndarray, y_hat: np.ndarray) -> dict[str, float]:
    from sklearn.metrics import accuracy_score, balanced_accuracy_score

    return {
        "accuracy": float(accuracy_score(y, y_hat)),
        "balanced_accuracy": float(balanced_accuracy_score(y, y_hat)),
    }


def _top_feature_lines(
    model: Any,
    feature_names: list[str] | None,
    *,
    n: int = 12,
) -> str:
    imp = getattr(model, "feature_importances_", None)
    if imp is None or feature_names is None or len(feature_names) != len(imp):
        return ""
    order = np.argsort(imp)[::-1][:n]
    lines = []
    for i in order:
        lines.append(f"  {feature_names[i]}: {float(imp[i]):.4f}")
    return "\n".join(lines) if lines else ""


def fit_qsar_model(
    *,
    df: pd.DataFrame,
    oids: list[int],
    activity_column: str,
    feature_columns: list[str] | None,
    fp_choice: str | None,
    mol_rows: list[tuple[int, object]] | None,
    model_key: str,
    task_mode: str,
    train_fraction: float,
    cv_folds: int,
    standardize: bool,
) -> QSARFitResult:
    """
    Fit a QSAR model on rows with known activity; return metrics and a prediction bundle.
    """
    from sklearn.model_selection import cross_val_score, train_test_split

    X, y_raw, feat_names, n_numeric, feat_summary = _activity_and_features(
        df=df,
        oids=oids,
        activity_column=activity_column,
        feature_columns=feature_columns,
        mol_rows=mol_rows,
        fp_choice=fp_choice,
    )
    standardize_numeric = bool(standardize and n_numeric > 0)

    force_task: TaskKind | None = None
    if task_mode == "regression":
        force_task = "regression"
    elif task_mode == "classification":
        force_task = "classification"
    task = infer_task_type(y_raw, force=force_task)
    y, class_labels = _prepare_labels(y_raw, task)

    if task == "classification" and class_labels is not None and len(class_labels) < 2:
        raise ValueError("Classification needs at least two distinct activity classes.")

    model = _make_model(task, model_key)
    Xs, scaler = _scale_fit(X, standardize_numeric=standardize_numeric, n_numeric=n_numeric)

    test_size = max(0.1, min(0.4, 1.0 - float(train_fraction)))
    stratify = y if task == "classification" and len(np.unique(y)) > 1 else None
    try:
        X_train, X_test, y_train, y_test = train_test_split(
            Xs,
            y,
            test_size=test_size,
            random_state=42,
            stratify=stratify,
        )
    except ValueError as exc:
        raise ValueError(
            "Could not split data for training (too few samples per class?). "
            f"Details: {exc}"
        ) from exc

    model.fit(X_train, y_train)
    y_hat = model.predict(X_test)

    lines: list[str] = [feat_summary, ""]
    lines.append(f"Task: {task}")
    lines.append(f"Model: {_model_label(task, model_key)}")
    lines.append(f"Training rows: {len(y)}  |  Features: {X.shape[1]}")
    lines.append(f"Hold-out fraction: {test_size:.0%}  ({len(y_test)} test rows)")
    lines.append("")

    if task == "regression":
        m = _regression_metrics(y_test, y_hat)
        lines.append("Hold-out metrics:")
        lines.append(f"  R² = {m['r2']:.4f}")
        lines.append(f"  RMSE = {m['rmse']:.4f}")
        lines.append(f"  MAE = {m['mae']:.4f}")
        scoring = "r2"
    else:
        m = _classification_metrics(y_test, y_hat)
        lines.append("Hold-out metrics:")
        lines.append(f"  Accuracy = {m['accuracy']:.4f}")
        lines.append(f"  Balanced accuracy = {m['balanced_accuracy']:.4f}")
        if class_labels is not None:
            lines.append(f"  Classes: {', '.join(str(c) for c in class_labels)}")
        scoring = "accuracy"

    folds = max(2, min(int(cv_folds), len(y_train)))
    if len(y_train) >= folds + 1:
        try:
            cv_scores = cross_val_score(model, X_train, y_train, cv=folds, scoring=scoring, n_jobs=-1)
            lines.append("")
            lines.append(f"{folds}-fold CV on training set ({scoring}):")
            lines.append(f"  mean = {float(np.mean(cv_scores)):.4f}  std = {float(np.std(cv_scores)):.4f}")
        except Exception as exc:
            lines.append("")
            lines.append(f"Cross-validation skipped: {exc}")

    top_feat_eval = _top_feature_lines(model, feat_names)

    deploy = _make_model(task, model_key)
    deploy.fit(Xs, y)
    top_feat = _top_feature_lines(deploy, feat_names) or top_feat_eval
    if top_feat:
        lines.append("")
        lines.append("Top feature importances (full-data model):")
        lines.append(top_feat)
    bundle = QSARModelBundle(
        task=task,
        model_key=model_key,
        activity_column=activity_column,
        feature_columns=tuple(feature_columns) if feature_columns else None,
        fp_choice=fp_choice,
        n_numeric_features=n_numeric,
        standardize_numeric=standardize_numeric,
        model=deploy,
        scaler=scaler,
        class_labels=class_labels,
    )

    return QSARFitResult(
        task=task,
        model_key=model_key,
        activity_column=activity_column,
        metrics_text="\n".join(lines),
        bundle=bundle,
        n_train=len(y),
        n_features=int(X.shape[1]),
        top_features_text=top_feat,
    )


def _model_label(task: TaskKind, model_key: str) -> str:
    if task == "regression":
        return REGRESSION_MODELS.get(model_key, model_key)
    return CLASSIFICATION_MODELS.get(model_key, model_key)


def predict_qsar_rows(
    bundle: QSARModelBundle,
    *,
    df: pd.DataFrame,
    oids: list[int],
    mol_rows: list[tuple[int, object]] | None,
    output_column: str | None = None,
) -> list[tuple[int, dict[str, str]]]:
    """Predict activity for all in-scope rows with valid features."""
    out_col = (output_column or "").strip() or f"QSAR_{bundle.activity_column}"

    from .feature_matrix import build_combined_feature_matrix

    if not bundle.feature_columns and not bundle.fp_choice:
        return []
    try:
        built = build_combined_feature_matrix(
            df=df,
            oids=oids,
            feature_columns=list(bundle.feature_columns) if bundle.feature_columns else None,
            mol_rows=mol_rows,
            fp_choice=bundle.fp_choice,
            min_rows=1,
        )
    except ValueError:
        return []
    X = built.X
    pred_oids = built.oids

    Xs = _scale_apply(X, bundle.scaler, n_numeric=bundle.n_numeric_features)
    raw = bundle.model.predict(Xs)
    results: list[tuple[int, dict[str, str]]] = []
    if bundle.task == "classification" and bundle.class_labels is not None:
        for oid, pred_i in zip(pred_oids, raw):
            lab = bundle.class_labels[int(pred_i)]
            results.append((int(oid), {out_col: str(lab)}))
    else:
        for oid, val in zip(pred_oids, raw):
            results.append((int(oid), {out_col: f"{float(val):.6g}"}))
    return results


def models_for_task(task: TaskKind) -> list[tuple[str, str]]:
    """Return (key, label) pairs for UI combo."""
    if task == "regression":
        return list(REGRESSION_MODELS.items())
    return list(CLASSIFICATION_MODELS.items())
