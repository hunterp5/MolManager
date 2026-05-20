"""Process pool shutdown helpers (app exit / cancel)."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ProcessPoolExecutor

from molmanager.workers import process_pool_utils as ppu


def _sleep_worker(_: int) -> int:
    time.sleep(30)
    return 1


def test_shutdown_all_kills_registered_pool() -> None:
    ppu._SHUTDOWN.clear()
    ex = ppu.register_process_pool(ProcessPoolExecutor(max_workers=1))
    try:
        ex.submit(_sleep_worker, 0)
        time.sleep(0.3)
        ppu.shutdown_all_process_pools(kill_workers=True)
        processes = getattr(ex, "_processes", None) or {}
        assert not any(p is not None and p.is_alive() for p in processes.values())
    finally:
        ppu.shutdown_process_pool_executor(ex, kill_workers=True)


def test_should_terminate_on_cancel_or_shutdown() -> None:
    ppu._SHUTDOWN.clear()
    ev = threading.Event()
    assert not ppu.should_terminate_process_pool(ev)
    ev.set()
    assert ppu.should_terminate_process_pool(ev)
    ev.clear()
    ppu.signal_application_shutdown()
    assert ppu.should_terminate_process_pool(None)
    ppu._SHUTDOWN.clear()
