"""Central configuration from environment."""

from __future__ import annotations


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


def test_protomer_process_workers_env(monkeypatch):
    monkeypatch.setenv("CHEMMANAGER_PROTOmer_PROCESSES", "3")
    assert load_config().protomer_process_workers == 3


def test_pka_process_workers_env(monkeypatch):
    monkeypatch.setenv("CHEMMANAGER_PKA_PROCESS_WORKERS", "2")
    assert load_config().pka_process_workers == 2


def test_disable_custom_calc_env(monkeypatch):
    monkeypatch.setenv("CHEMMANAGER_DISABLE_CUSTOM_CALC", "1")
    assert load_config().disable_custom_calc is True


def test_perf_and_sqlite_page_size_env(monkeypatch):
    monkeypatch.setenv("CHEMMANAGER_PERF_METRICS", "true")
    monkeypatch.setenv("CHEMMANAGER_PERF_LOG_EVERY", "5")
    monkeypatch.setenv("CHEMMANAGER_SQLITE_BACKEND_PAGE_SIZE", "6000")
    cfg = load_config()
    assert cfg.perf_metrics_enabled is True
    assert cfg.perf_log_every == 5
    assert cfg.sqlite_backend_page_size == 6000


def test_sqlite_backend_page_size_default():
    cfg = load_config()
    assert cfg.sqlite_backend_page_size == 5000


def test_structure_render_limits_env(monkeypatch):
    monkeypatch.setenv("CHEMMANAGER_AUTO_RENDER_2D_MAX_ROWS", "12000")
    monkeypatch.setenv("CHEMMANAGER_STRUCTURE_RENDER_LAZY_MIN_ROWS", "6000")
    monkeypatch.setenv("CHEMMANAGER_STRUCTURE_RENDER_PIXMAP_LRU", "128")
    cfg = load_config()
    assert cfg.auto_render_2d_max_rows == 12000
    assert cfg.structure_render_lazy_min_rows == 6000
    assert cfg.structure_render_pixmap_lru == 128


def test_tool_progress_poll_ms_env(monkeypatch):
    monkeypatch.setenv("CHEMMANAGER_TOOL_PROGRESS_POLL_MS", "350")
    assert load_config().tool_progress_poll_ms == 350
