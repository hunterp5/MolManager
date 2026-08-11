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
