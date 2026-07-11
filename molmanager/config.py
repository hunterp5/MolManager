"""Central environment-driven settings (see README env table)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str) -> str:
    raw = (os.environ.get(name) or "").strip()
    return raw if raw else default


def _env_int(name: str, default: int, *, lo: int, hi: int | None = None) -> int:
    try:
        v = int((os.environ.get(name) or "").strip() or str(default))
    except ValueError:
        v = default
    v = max(lo, v)
    if hi is not None:
        v = min(v, hi)
    return v


def _env_optional_positive_int(name: str, *, lo: int, hi: int) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
    except ValueError:
        return None
    return max(lo, min(v, hi))


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return default


def _env_float(name: str, default: float, *, lo: float) -> float:
    try:
        v = float((os.environ.get(name) or "").strip() or str(default))
    except ValueError:
        v = default
    return max(lo, v)


def clamp_substructure_async_rows(raw: str | None) -> int:
    """Match ``FilterPanelMixin`` / worker threshold semantics."""
    default = 400
    try:
        thresh = int((raw or "").strip() or str(default))
    except ValueError:
        thresh = default
    return max(64, min(thresh, 500_000))


@dataclass(frozen=True)
class MolManagerConfig:
    log_level: str
    max_threadpool: int | None
    render_threadpool: int | None
    substructure_async_rows: int
    sql_max_rows_hard: int
    sql_precount_warn: int
    sqlite_timeout_s: float
    pg_connect_timeout: int
    conformer_threads: int | None
    descriptor_threads: int | None
    descriptor_process_pool_min_rows: int
    descriptor_fp_process_pool_min_rows: int
    descriptor_process_pool_batch_size: int
    background_job_poll_ms: int
    bulk_update_defer_color_cache_rows: int
    protomer_process_workers: int | None
    pka_process_workers: int | None
    disable_custom_calc: bool
    custom_calc_legacy_eval: bool
    filter_debounce_substructure_rows: int
    filter_debounce_substructure_ms: int
    filter_debounce_default_rows: int
    filter_debounce_default_ms: int
    filter_async_min_rows: int
    filter_chunk_rows: int
    bounds_async_min_rows: int
    bounds_chunk_rows: int
    ingest_gui_chunk_size: int
    ingest_gui_time_budget_ms: int
    ingest_worker_batch_size: int
    ingest_csv_text_first: bool
    ingest_silent_model_append: bool
    structure_render_lazy_after_ingest_min_rows: int
    perf_metrics_enabled: bool
    perf_log_every: int
    sqlite_backend_page_size: int
    auto_render_2d_max_rows: int
    structure_render_lazy_min_rows: int
    structure_render_pixmap_lru: int
    tool_progress_poll_ms: int
    table_selection_oid_override_min: int
    table_selection_chunk_rows: int
    table_delete_batch_min: int
    table_delete_chunk_rows: int
    recomp_constraint_candidate_multiplier: int
    recomp_constraint_min_candidates: int


def _migrate_chemmanager_env_aliases() -> None:
    """Map legacy ``CHEMMANAGER_*`` variables to ``MOLMANAGER_*`` when the new name is unset."""
    old_prefix = "CHEMMANAGER_"
    new_prefix = "MOLMANAGER_"
    for key, value in list(os.environ.items()):
        if not key.startswith(old_prefix):
            continue
        new_key = new_prefix + key[len(old_prefix) :]
        if new_key not in os.environ:
            os.environ[new_key] = value


def load_config() -> MolManagerConfig:
    """Read current settings from ``os.environ`` (no process-wide cache — tests can monkeypatch)."""
    _migrate_chemmanager_env_aliases()
    hard = _env_int("MOLMANAGER_SQL_MAX_ROWS_HARD", 2_000_000, lo=1000, hi=50_000_000)
    precowarn = _env_int("MOLMANAGER_SQL_PRECOUNT_WARN", 100_000, lo=1000, hi=hard)
    return MolManagerConfig(
        log_level=_env_str("MOLMANAGER_LOG_LEVEL", "INFO").upper(),
        max_threadpool=_env_optional_positive_int("MOLMANAGER_MAX_THREADPOOL", lo=1, hi=64),
        render_threadpool=_env_optional_positive_int("MOLMANAGER_RENDER_THREADPOOL", lo=1, hi=32),
        substructure_async_rows=clamp_substructure_async_rows(os.environ.get("MOLMANAGER_SUBSTRUCTURE_ASYNC_ROWS")),
        sql_max_rows_hard=hard,
        sql_precount_warn=precowarn,
        sqlite_timeout_s=_env_float("MOLMANAGER_SQLITE_TIMEOUT_S", 30.0, lo=0.1),
        pg_connect_timeout=_env_int("MOLMANAGER_PG_CONNECT_TIMEOUT", 30, lo=1, hi=3600),
        conformer_threads=_env_optional_positive_int("MOLMANAGER_CONFORMER_THREADS", lo=1, hi=16),
        descriptor_threads=_env_optional_positive_int("MOLMANAGER_DESCRIPTOR_THREADS", lo=1, hi=32),
        descriptor_process_pool_min_rows=_env_int(
            "MOLMANAGER_DESCRIPTOR_PROCESS_POOL_MIN_ROWS", 1500, lo=0, hi=10_000_000
        ),
        descriptor_fp_process_pool_min_rows=_env_int(
            "MOLMANAGER_DESCRIPTOR_FP_PROCESS_POOL_MIN_ROWS", 64, lo=2, hi=10_000_000
        ),
        descriptor_process_pool_batch_size=_env_int(
            "MOLMANAGER_DESCRIPTOR_PROCESS_POOL_BATCH_SIZE", 32, lo=1, hi=512
        ),
        background_job_poll_ms=_env_int(
            "MOLMANAGER_BACKGROUND_JOB_POLL_MS", 500, lo=100, hi=5000
        ),
        bulk_update_defer_color_cache_rows=_env_int(
            "MOLMANAGER_BULK_UPDATE_DEFER_COLOR_CACHE_ROWS", 5000, lo=0, hi=10_000_000
        ),
        protomer_process_workers=_env_optional_positive_int("MOLMANAGER_PROTOMER_PROCESSES", lo=1, hi=8),
        pka_process_workers=_env_optional_positive_int("MOLMANAGER_PKA_PROCESS_WORKERS", lo=1, hi=8),
        disable_custom_calc=_env_truthy("MOLMANAGER_DISABLE_CUSTOM_CALC"),
        custom_calc_legacy_eval=_env_truthy("MOLMANAGER_CUSTOM_CALC_LEGACY_EVAL"),
        filter_debounce_substructure_rows=_env_int(
            "MOLMANAGER_FILTER_DEBOUNCE_SUBSTRUCTURE_ROWS", 120, lo=1, hi=1_000_000
        ),
        filter_debounce_substructure_ms=_env_int(
            "MOLMANAGER_FILTER_DEBOUNCE_SUBSTRUCTURE_MS", 85, lo=0, hi=60_000
        ),
        filter_debounce_default_rows=_env_int(
            "MOLMANAGER_FILTER_DEBOUNCE_DEFAULT_ROWS", 0, lo=0, hi=1_000_000
        ),
        filter_debounce_default_ms=_env_int(
            "MOLMANAGER_FILTER_DEBOUNCE_DEFAULT_MS", 80, lo=0, hi=60_000
        ),
        filter_async_min_rows=_env_int(
            "MOLMANAGER_FILTER_ASYNC_MIN_ROWS", 5000, lo=1, hi=10_000_000
        ),
        filter_chunk_rows=_env_int(
            "MOLMANAGER_FILTER_CHUNK_ROWS", 2000, lo=64, hi=100_000
        ),
        bounds_async_min_rows=_env_int(
            "MOLMANAGER_BOUNDS_ASYNC_MIN_ROWS", 5000, lo=1, hi=10_000_000
        ),
        bounds_chunk_rows=_env_int(
            "MOLMANAGER_BOUNDS_CHUNK_ROWS", 2000, lo=64, hi=100_000
        ),
        ingest_gui_chunk_size=_env_int("MOLMANAGER_INGEST_GUI_CHUNK", 512, lo=16, hi=10_000),
        ingest_gui_time_budget_ms=_env_int("MOLMANAGER_INGEST_GUI_TIME_MS", 30, lo=5, hi=200),
        ingest_worker_batch_size=_env_int("MOLMANAGER_INGEST_WORKER_BATCH", 2000, lo=64, hi=20_000),
        ingest_csv_text_first=_env_bool("MOLMANAGER_INGEST_CSV_TEXT_FIRST", True),
        ingest_silent_model_append=_env_bool("MOLMANAGER_INGEST_SILENT_MODEL_APPEND", True),
        structure_render_lazy_after_ingest_min_rows=_env_int(
            "MOLMANAGER_STRUCTURE_RENDER_LAZY_AFTER_INGEST", 200, lo=0, hi=10_000_000
        ),
        perf_metrics_enabled=_env_truthy("MOLMANAGER_PERF_METRICS"),
        perf_log_every=_env_int("MOLMANAGER_PERF_LOG_EVERY", 25, lo=1, hi=10_000),
        sqlite_backend_page_size=_env_int("MOLMANAGER_SQLITE_BACKEND_PAGE_SIZE", 5000, lo=100, hi=200_000),
        auto_render_2d_max_rows=_env_int(
            "MOLMANAGER_AUTO_RENDER_2D_MAX_ROWS", 8_000, lo=0, hi=10_000_000
        ),
        structure_render_lazy_min_rows=_env_int(
            "MOLMANAGER_STRUCTURE_RENDER_LAZY_MIN_ROWS", 5_000, lo=500, hi=10_000_000
        ),
        structure_render_pixmap_lru=_env_int(
            "MOLMANAGER_STRUCTURE_RENDER_PIXMAP_LRU", 384, lo=32, hi=4096
        ),
        tool_progress_poll_ms=_env_int("MOLMANAGER_TOOL_PROGRESS_POLL_MS", 200, lo=50, hi=2000),
        table_selection_oid_override_min=_env_int(
            "MOLMANAGER_TABLE_SELECTION_OID_OVERRIDE_MIN", 2500, lo=100, hi=10_000_000
        ),
        table_selection_chunk_rows=_env_int(
            "MOLMANAGER_TABLE_SELECTION_CHUNK_ROWS", 2000, lo=64, hi=100_000
        ),
        table_delete_batch_min=_env_int(
            "MOLMANAGER_TABLE_DELETE_BATCH_MIN", 500, lo=1, hi=10_000_000
        ),
        table_delete_chunk_rows=_env_int(
            "MOLMANAGER_TABLE_DELETE_CHUNK_ROWS", 2000, lo=64, hi=100_000
        ),
        recomp_constraint_candidate_multiplier=_env_int(
            "MOLMANAGER_RECOMP_CONSTRAINT_CANDIDATE_MULT", 100, lo=10, hi=10_000
        ),
        recomp_constraint_min_candidates=_env_int(
            "MOLMANAGER_RECOMP_CONSTRAINT_MIN_CANDIDATES", 10_000, lo=1000, hi=10_000_000
        ),
    )
