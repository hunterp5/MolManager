"""End-to-end offscreen UI workflow benchmark for large tables."""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtWidgets import QApplication

from molmanager.ui.filters.cards import SubstructureFilterCard, TextFilterCard
from molmanager.ui.main_window import ChemicalTableApp
from molmanager.workers.export_worker import ExportWorker


class _NoopEmitter:
    def emit(self, *_args, **_kwargs) -> None:
        return


class _NoopSignals:
    export_finished = _NoopEmitter()
    tool_progress = _NoopEmitter()


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * p))))
    return float(vals[idx])


def _write_session_csv(path: Path, n_rows: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["SMILES", "MW", "Note"])
        writer.writeheader()
        for i in range(n_rows):
            smi = "CCO" if i % 2 == 0 else "CCN"
            writer.writerow(
                {
                    "SMILES": smi,
                    "MW": f"{100.0 + ((i * 7) % 450):.3f}",
                    "Note": f"group_{i % 25}",
                }
            )


def _build_export_snapshot(app: ChemicalTableApp) -> dict[int, dict[str, str]]:
    cols = [h for h in app.headers if h not in ("ID_HIDDEN", "Structure")]
    h_map = {h: i for i, h in enumerate(app.headers)}
    out: dict[int, dict[str, str]] = {}
    for r in range(app._table_model.rowCount()):
        oid = app._table_model.row_oid(r)
        out[oid] = {h: app._export_cell_text(r, h_map[h]) for h in cols}
    return out


def _bench_one(scale: int) -> dict[str, float]:
    app = ChemicalTableApp()
    tmp_dir = Path(tempfile.mkdtemp(prefix="MOLMANAGER_ui_bench_"))
    csv_path = tmp_dir / f"session_{scale}.csv"
    out_csv = tmp_dir / f"export_{scale}.csv"
    _write_session_csv(csv_path, scale)

    tracemalloc.start()
    t0 = time.perf_counter()
    app.load_session_csv(str(csv_path))
    load_ms = (time.perf_counter() - t0) * 1000.0
    cur_b, peak_b = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    _ = cur_b

    app.delete_all_filters_from_panel()
    text_card = TextFilterCard(["SMILES", "MW", "Note"], app)
    text_card.set_column("Note")
    text_card.text_edit.setText("group_7")
    app.filters = [text_card]
    t0 = time.perf_counter()
    app._apply_filters_impl_sync(None)
    text_filter_ms = (time.perf_counter() - t0) * 1000.0

    app.delete_all_filters_from_panel()
    ss_card = SubstructureFilterCard()
    ss_card.set_smarts("CO")
    app.filters = [ss_card]
    t0 = time.perf_counter()
    app._apply_filters_impl_sync(None)
    substructure_ms = (time.perf_counter() - t0) * 1000.0

    app.delete_all_filters_from_panel()
    app._search_panel.setVisible(True)
    app._populate_table_search_columns_combo()
    note_col = app.headers.index("Note")
    for j in range(app._search_col_combo.count()):
        if app._search_col_combo.itemData(j) == note_col:
            app._search_col_combo.setCurrentIndex(j)
            break
    app._search_partial_cb.setChecked(True)
    app._search_substructure_cb.setChecked(False)
    app._search_query_edit.setText("group_11")
    t0 = time.perf_counter()
    app._run_table_search()
    search_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    snap = _build_export_snapshot(app)
    export_snapshot_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    worker = ExportWorker(
        path=str(out_csv),
        ext=".csv",
        mols_dict=dict(app.mols),
        headers_to_export=list(app.headers),
        table_data=snap,
        signals=_NoopSignals(),
    )
    worker.run()
    export_write_ms = (time.perf_counter() - t0) * 1000.0

    qapp = QApplication.instance()
    if qapp is not None:
        qapp.processEvents()
    app.close()

    return {
        "load_ms": load_ms,
        "text_filter_ms": text_filter_ms,
        "substructure_ms": substructure_ms,
        "search_ms": search_ms,
        "export_snapshot_ms": export_snapshot_ms,
        "export_write_ms": export_write_ms,
        "peak_mem_mb": peak_b / (1024.0 * 1024.0),
    }


def run_benchmark(scales: list[int], runs: int) -> None:
    _app = QApplication.instance() or QApplication([])
    for scale in scales:
        samples = [_bench_one(scale) for _ in range(runs)]
        print(f"\nRows: {scale:,}")
        for key in (
            "load_ms",
            "text_filter_ms",
            "substructure_ms",
            "search_ms",
            "export_snapshot_ms",
            "export_write_ms",
            "peak_mem_mb",
        ):
            vals = [float(s[key]) for s in samples]
            avg = statistics.fmean(vals)
            p50 = _percentile(vals, 0.50)
            p95 = _percentile(vals, 0.95)
            unit = "MB" if key.endswith("_mb") else "ms"
            print(f"  {key:18s} avg={avg:9.1f}{unit}  p50={p50:9.1f}{unit}  p95={p95:9.1f}{unit}")


def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-end UI workflow benchmark.")
    parser.add_argument("--runs", type=int, default=1, help="Runs per scale (default: 1)")
    parser.add_argument(
        "--scales",
        type=str,
        default="10000,50000,100000",
        help="Comma-separated row counts (default: 10000,50000,100000)",
    )
    args = parser.parse_args()
    scales = [max(100, int(x.strip())) for x in (args.scales or "").split(",") if x.strip()]
    run_benchmark(scales, max(1, int(args.runs)))


if __name__ == "__main__":
    main()

