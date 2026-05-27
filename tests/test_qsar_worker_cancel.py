"""QSAR workers report cancellation via failed signal."""

from __future__ import annotations

import threading

import pytest
from PyQt5.QtCore import QObject

from molmanager.workers.qsar_worker import QSARSignals, QSARPredictWorker, QSARTrainWorker


class _Collector(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def on_failed(self, msg: str) -> None:
        self.messages.append(msg)


def test_qsar_train_worker_emits_failed_when_cancelled_before_run() -> None:
    signals = QSARSignals()
    collector = _Collector()
    signals.failed.connect(collector.on_failed)
    cancel = threading.Event()
    cancel.set()
    worker = QSARTrainWorker({"oids": [1], "activity_column": "y"}, signals, cancel_event=cancel)
    worker.run()
    assert collector.messages == ["Cancelled."]


def test_qsar_predict_worker_emits_failed_when_cancelled_before_run() -> None:
    signals = QSARSignals()
    collector = _Collector()
    signals.failed.connect(collector.on_failed)
    cancel = threading.Event()
    cancel.set()
    worker = QSARPredictWorker({"bundle": None, "oids": [], "dataframe": None}, signals, cancel_event=cancel)
    worker.run()
    assert collector.messages == ["Cancelled."]
