"""Central configuration from environment."""

from __future__ import annotations

import pytest

from chemmanager.config import clamp_substructure_async_rows, load_config


def test_clamp_substructure_async_rows_bounds():
    assert clamp_substructure_async_rows("") == 400
    assert clamp_substructure_async_rows("64") == 64
    assert clamp_substructure_async_rows("10") == 64
    assert clamp_substructure_async_rows("999999") == 500_000


def test_load_config_sql_caps_respect_monkeypatch(monkeypatch):
    monkeypatch.setenv("CHEMMANAGER_SQL_MAX_ROWS_HARD", "5000")
    monkeypatch.setenv("CHEMMANAGER_SQL_PRECOUNT_WARN", "2000")
    cfg = load_config()
    assert cfg.sql_max_rows_hard == 5000
    assert cfg.sql_precount_warn == 2000


def test_disable_custom_calc_env(monkeypatch):
    monkeypatch.setenv("CHEMMANAGER_DISABLE_CUSTOM_CALC", "1")
    assert load_config().disable_custom_calc is True
