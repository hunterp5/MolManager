"""Large descriptor jobs stream progressive result chunks before the terminal signal."""

from __future__ import annotations

from unittest.mock import patch

from molmanager.workers.chemistry_tools import CalcWorker
from molmanager.workers.signals import WorkerSignals


def test_calc_worker_streams_partial_chunks_for_large_job(monkeypatch):
    """
    With streaming enabled, CalcWorker must emit ``calculated_partial`` chunks covering every row
    and still emit the terminal ``calculated`` with the full result set.
    """
    monkeypatch.setenv("MOLMANAGER_DESCRIPTOR_STREAM_MIN_ROWS", "1")
    monkeypatch.setenv("MOLMANAGER_DESCRIPTOR_STREAM_CHUNK_ROWS", "2")

    sigs = WorkerSignals()
    partial_rows: list = []
    final: dict[str, object] = {}
    sigs.calculated_partial.connect(lambda rows, headers: partial_rows.extend(rows))
    sigs.calculated.connect(lambda rows, headers: final.update({"rows": rows, "headers": headers}))

    data = [(i, "CCO") for i in range(1, 7)]
    disp_headers = ["MolWt"]

    with patch(
        "molmanager.workers.chemistry_tools._calc_descriptor_row_task",
        side_effect=lambda task: (task[0], {"MolWt": f"v{task[0]}"}),
    ):
        worker = CalcWorker(
            data=data,
            disp_headers=disp_headers,
            int_fns=["MolWt"],
            is_smiles=True,
            signals=sigs,
        )
        worker.run()

    assert {oid for oid, _ in partial_rows} == {1, 2, 3, 4, 5, 6}
    assert len(partial_rows) == 6
    assert {oid for oid, _ in final.get("rows", [])} == {1, 2, 3, 4, 5, 6}
    assert final.get("headers") == disp_headers


def test_calc_worker_no_streaming_below_threshold(monkeypatch):
    """Below the streaming threshold, no progressive chunks are emitted (single terminal apply)."""
    monkeypatch.setenv("MOLMANAGER_DESCRIPTOR_STREAM_MIN_ROWS", "1000")

    sigs = WorkerSignals()
    partial_rows: list = []
    final: dict[str, object] = {}
    sigs.calculated_partial.connect(lambda rows, headers: partial_rows.extend(rows))
    sigs.calculated.connect(lambda rows, headers: final.update({"rows": rows}))

    data = [(i, "CCO") for i in range(1, 5)]

    with patch(
        "molmanager.workers.chemistry_tools._calc_descriptor_row_task",
        side_effect=lambda task: (task[0], {"MolWt": f"v{task[0]}"}),
    ):
        CalcWorker(
            data=data,
            disp_headers=["MolWt"],
            int_fns=["MolWt"],
            is_smiles=True,
            signals=sigs,
        ).run()

    assert partial_rows == []
    assert {oid for oid, _ in final.get("rows", [])} == {1, 2, 3, 4}
