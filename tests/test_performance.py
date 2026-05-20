from __future__ import annotations

from chemmanager.performance import PerformanceTracker


def test_performance_tracker_snapshot_and_counts():
    tr = PerformanceTracker(enabled=True, log_every=999, window_size=64)
    for i in range(1, 21):
        tr.record("load_rows", float(i))
    snap = tr.snapshot("load_rows")
    assert snap is not None
    assert snap.count == 20
    assert snap.max_ms == 20.0
    assert snap.p50_ms >= 10.0
    assert snap.p95_ms >= snap.p50_ms

