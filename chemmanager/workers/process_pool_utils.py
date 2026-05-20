"""Helpers to stop :class:`ProcessPoolExecutor` workers promptly on cancel or app exit."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ProcessPoolExecutor

logger = logging.getLogger(__name__)

_SHUTDOWN = threading.Event()
_ACTIVE_POOLS: list[ProcessPoolExecutor] = []
_LOCK = threading.Lock()


def signal_application_shutdown() -> None:
    """Set once when the main window begins closing (workers should exit quickly)."""
    _SHUTDOWN.set()


def application_is_shutting_down() -> bool:
    return _SHUTDOWN.is_set()


def should_terminate_process_pool(cancel_event: threading.Event | None = None) -> bool:
    """True when pool child processes should be killed, not only cancelled."""
    if application_is_shutting_down():
        return True
    return cancel_event is not None and cancel_event.is_set()


def register_process_pool(ex: ProcessPoolExecutor) -> ProcessPoolExecutor:
    with _LOCK:
        _ACTIVE_POOLS.append(ex)
    return ex


def unregister_process_pool(ex: ProcessPoolExecutor) -> None:
    with _LOCK:
        try:
            _ACTIVE_POOLS.remove(ex)
        except ValueError:
            pass


def shutdown_process_pool_executor(
    ex: ProcessPoolExecutor | None,
    *,
    kill_workers: bool = False,
) -> None:
    """Shut down a pool without blocking; optionally ``terminate()`` child processes (Windows pKa jobs)."""
    if ex is None:
        return
    unregister_process_pool(ex)
    try:
        ex.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        try:
            ex.shutdown(wait=False)
        except Exception:
            logger.debug("process pool shutdown failed", exc_info=True)
    except Exception:
        logger.debug("process pool shutdown failed", exc_info=True)
    if kill_workers:
        _terminate_executor_children(ex)


def _terminate_executor_children(ex: ProcessPoolExecutor) -> None:
    processes = getattr(ex, "_processes", None)
    if not processes:
        return
    procs = list(processes.values())
    for proc in procs:
        if proc is None:
            continue
        try:
            if proc.is_alive():
                proc.terminate()
        except Exception:
            logger.debug("terminate process-pool child failed", exc_info=True)
    for proc in procs:
        if proc is None:
            continue
        try:
            proc.join(timeout=0.5)
        except Exception:
            pass


def shutdown_all_process_pools(*, kill_workers: bool = False) -> None:
    """Stop every pool still registered (e.g. main-window close while pKa is running)."""
    with _LOCK:
        pools = list(_ACTIVE_POOLS)
        _ACTIVE_POOLS.clear()
    for ex in pools:
        shutdown_process_pool_executor(ex, kill_workers=kill_workers)
