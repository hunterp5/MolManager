"""Pytest fixtures for molmanager (Qt offscreen for headless CI)."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _reset_worker_global_state() -> None:
    """Avoid cross-test pollution of process-pool shutdown (pKa / pkasolver workers)."""
    from molmanager.workers import process_pool_utils as ppu

    ppu._SHUTDOWN.clear()
    yield
    ppu._SHUTDOWN.clear()


@pytest.fixture(scope="session")
def qapp():
    """Single QApplication for the test session."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
