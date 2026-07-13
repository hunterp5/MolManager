"""Hard caps and preflight checks for large memory-heavy tools."""

from __future__ import annotations

from dataclasses import dataclass

from .config import load_config
from .memory_usage import current_process_rss_bytes, format_memory_bytes


@dataclass(frozen=True)
class MemoryGuardResult:
    """Outcome of a preflight check before starting a heavy job."""

    ok: bool
    message: str = ""
    estimated_extra_bytes: int | None = None


def _rss_hint() -> str:
    rss = current_process_rss_bytes()
    if rss is None:
        return ""
    return f" Current process memory: {format_memory_bytes(rss)}."


def check_conformer_workload(n_rows: int, num_confs: int) -> MemoryGuardResult:
    """Guard conformer generation by row count and rows×confs product."""
    cfg = load_config()
    rows = max(0, int(n_rows))
    confs = max(1, int(num_confs))
    product = rows * confs
    if rows > cfg.memory_guard_conf_max_rows:
        return MemoryGuardResult(
            False,
            f"Conformer generation is limited to {cfg.memory_guard_conf_max_rows:,} rows "
            f"(requested {rows:,}). Narrow the scope or lower the row set."
            + _rss_hint(),
        )
    if product > cfg.memory_guard_conf_max_row_confs:
        return MemoryGuardResult(
            False,
            f"Conformer workload {rows:,} rows × {confs:,} confs = {product:,} exceeds the "
            f"cap of {cfg.memory_guard_conf_max_row_confs:,}. Reduce scope and/or conformer count."
            + _rss_hint(),
        )
    # Rough peak: ~0.5–2 MiB per multi-conf mol retained in worker results (conservative).
    est = product * 8_000
    return MemoryGuardResult(True, estimated_extra_bytes=est)


def check_cluster_workload(n_rows: int) -> MemoryGuardResult:
    cfg = load_config()
    rows = max(0, int(n_rows))
    if rows > cfg.memory_guard_cluster_max_rows:
        return MemoryGuardResult(
            False,
            f"Clustering is limited to {cfg.memory_guard_cluster_max_rows:,} structures "
            f"(requested {rows:,}). Select fewer rows or raise "
            f"MOLMANAGER_MEMORY_GUARD_CLUSTER_MAX_ROWS."
            + _rss_hint(),
        )
    # Pairwise float32 distances worst-case ~ n²/2 × 4 bytes (upper bound hint).
    est = (rows * rows // 2) * 4 if rows > 1 else 0
    return MemoryGuardResult(True, estimated_extra_bytes=est)


def check_fp_matrix_workload(n_rows: int, n_bits: int = 2048) -> MemoryGuardResult:
    cfg = load_config()
    rows = max(0, int(n_rows))
    bits = max(1, int(n_bits))
    cells = rows * bits
    if cells > cfg.memory_guard_fp_matrix_max_cells:
        max_rows = max(1, cfg.memory_guard_fp_matrix_max_cells // bits)
        return MemoryGuardResult(
            False,
            f"Fingerprint matrix {rows:,} × {bits:,} exceeds the cell cap "
            f"({cfg.memory_guard_fp_matrix_max_cells:,}). Use at most ~{max_rows:,} rows "
            f"for this fingerprint size, or adjust MOLMANAGER_MEMORY_GUARD_FP_MATRIX_MAX_CELLS."
            + _rss_hint(),
        )
    est = cells * 8  # float64 working matrix
    return MemoryGuardResult(True, estimated_extra_bytes=est)


def check_product_enumeration(max_products: int) -> MemoryGuardResult:
    cfg = load_config()
    n = max(0, int(max_products))
    if n > cfg.memory_guard_enum_max_products:
        return MemoryGuardResult(
            False,
            f"Max products is capped at {cfg.memory_guard_enum_max_products:,} "
            f"(requested {n:,}). Lower Max products or raise "
            f"MOLMANAGER_MEMORY_GUARD_ENUM_MAX_PRODUCTS."
            + _rss_hint(),
        )
    return MemoryGuardResult(True, estimated_extra_bytes=n * 50_000)


def clamp_max_products_ui(value: int) -> int:
    """Clamp a dialog Max products spinbox to the configured hard cap."""
    cfg = load_config()
    return max(1, min(int(value), int(cfg.memory_guard_enum_max_products)))


def clamp_dimred_max_points(value: int) -> int:
    cfg = load_config()
    return max(100, min(int(value), int(cfg.memory_guard_dimred_max_points)))
