"""Background QSAR training and prediction."""

from __future__ import annotations

import logging
import threading

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal

from ..qsar import QSARFitResult, fit_qsar_model, predict_qsar_rows

logger = logging.getLogger(__name__)


class QSARSignals(QObject):
    train_finished = pyqtSignal(object)
    predict_finished = pyqtSignal(list)
    failed = pyqtSignal(str)


class QSARTrainWorker(QRunnable):
    """Fit a QSAR model off the GUI thread."""

    def __init__(self, params: dict, signals: QSARSignals, cancel_event: threading.Event | None = None):
        super().__init__()
        self.params = dict(params)
        self.signals = signals
        self.cancel_event = cancel_event

    def run(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            return
        try:
            use_fp = bool(self.params.get("use_fingerprints"))
            result = fit_qsar_model(
                df=self.params["dataframe"],
                oids=list(self.params["oids"]),
                activity_column=str(self.params["activity_column"]),
                feature_columns=self.params.get("feature_columns"),
                fp_choice=self.params.get("fp_choice") if use_fp else None,
                mol_rows=self.params.get("mol_rows") if use_fp else None,
                model_key=str(self.params["model_key"]),
                task_mode=str(self.params.get("task_mode") or "auto"),
                train_fraction=float(self.params.get("train_fraction", 0.8)),
                cv_folds=int(self.params.get("cv_folds", 5)),
                standardize=bool(self.params.get("standardize", True)),
            )
            if self.cancel_event is not None and self.cancel_event.is_set():
                return
            self.signals.train_finished.emit(result)
        except Exception as exc:
            logger.exception("QSAR training failed")
            try:
                self.signals.failed.emit(str(exc) or exc.__class__.__name__)
            except Exception:
                pass


class QSARPredictWorker(QRunnable):
    """Apply a fitted QSAR model to in-scope rows."""

    def __init__(self, params: dict, signals: QSARSignals, cancel_event: threading.Event | None = None):
        super().__init__()
        self.params = dict(params)
        self.signals = signals
        self.cancel_event = cancel_event

    def run(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            return
        try:
            bundle = self.params["bundle"]
            rows = predict_qsar_rows(
                bundle,
                df=self.params["dataframe"],
                oids=list(self.params["oids"]),
                mol_rows=self.params.get("mol_rows"),
                output_column=self.params.get("output_column"),
            )
            if self.cancel_event is not None and self.cancel_event.is_set():
                return
            self.signals.predict_finished.emit(rows)
        except Exception as exc:
            logger.exception("QSAR prediction failed")
            try:
                self.signals.failed.emit(str(exc) or exc.__class__.__name__)
            except Exception:
                pass
