from __future__ import annotations

import re

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QFileDialog, QMessageBox

from ...workers import ExportWorker, UniversalLoadWorker
from ..strings import LOADING_DETAIL_APPEND, LOADING_DETAIL_READING_DISK


class IngestExportMixin:
    def open_file_dialog(self):
        f_filter = "All Supported (*.sdf *.mol *.csv *.smi *.txt *.tdt *.pdb);;SDF/Mol (*.sdf *.mol);;SMILES (*.smi *.txt *.csv);;TDT (*.tdt);;PDB (*.pdb)"
        path, _ = QFileDialog.getOpenFileName(self, "Open File", "", f_filter)
        if path:
            self.load_file(path)

    def open_import_file_dialog(self) -> None:
        f_filter = "All Supported (*.sdf *.mol *.csv *.smi *.txt *.tdt *.pdb);;SDF/Mol (*.sdf *.mol);;SMILES (*.smi *.txt *.csv);;TDT (*.tdt);;PDB (*.pdb)"
        path, _ = QFileDialog.getOpenFileName(self, "Import Data", "", f_filter)
        if path:
            self.import_file(path)

    def load_file(self, path: str) -> None:
        self._ingest_append_mode = False
        self.clear_all()
        self._ingest_loading = True
        self._structures_queued = 0
        self._import_building_progress_shown = False
        self._table_stack.setCurrentIndex(0)
        self._loading_detail.setText(LOADING_DETAIL_READING_DISK)
        self.status_label.setText("Reading file…")
        self.process_queue.enqueue(
            f"Open file: {path}",
            lambda ev, p=path, s=self.signals: UniversalLoadWorker(p, s, cancel_event=ev),
        )

    def import_file(self, path: str) -> None:
        """Load molecules from disk and append them to the current table (merge columns as needed)."""
        self._pending_batches = []
        self._processing_batches = False
        self._last_batch_received = False
        self._ingest_append_mode = True
        self._ingest_loading = True
        self._structures_queued = 0
        self._import_building_progress_shown = False
        self._table_stack.setCurrentIndex(0)
        self._loading_detail.setText(
            LOADING_DETAIL_APPEND
        )
        self.status_label.setText("Importing…")
        self.process_queue.enqueue(
            f"Import data: {path}",
            lambda ev, p=path, s=self.signals: UniversalLoadWorker(p, s, cancel_event=ev),
        )

    def _merge_import_headers(self, incoming: list[str]) -> None:
        """Extend ``self.headers`` / the table model with columns from an appended file."""
        old_tail = list(self.headers[2:]) if len(self.headers) > 2 else []
        inc_tail = list(incoming[2:]) if len(incoming) > 2 else []
        seen = set(old_tail)
        merged_tail = list(old_tail)
        for h in inc_tail:
            if h not in seen:
                seen.add(h)
                merged_tail.append(h)
        self.table.setSortingEnabled(False)
        old_set = set(old_tail)
        for h in merged_tail:
            if h not in old_set:
                nc = self._table_model.columnCount()
                self._table_model.insert_column_at(nc, h, None)
                old_set.add(h)
        self.headers = ["ID_HIDDEN", "Structure"] + merged_tail

    def run_export(self, selected=False):
        if not self.mols:
            return
        if getattr(self, "_export_busy", False):
            QMessageBox.warning(self, "Export", "An export is already in progress.")
            return
        t_mols = self.mols
        # Export in current visual column order (but always include ID/Structure first).
        vis_cols = self._visual_logical_columns()
        ordered_headers = [self.headers[i] for i in vis_cols if i < len(self.headers)]
        t_heads = ["ID_HIDDEN", "Structure"] + [h for h in ordered_headers if h not in ("ID_HIDDEN", "Structure")]
        if selected:
            items = self.table.selectionModel().selectedIndexes()
            if not items:
                return
            rows = sorted(list(set(i.row() for i in items)))
            t_mols = {}
            for r in rows:
                tr = self._table_model.cell_text(r, 0)
                if tr.isdigit():
                    k = int(tr)
                    if k in self.mols:
                        t_mols[k] = self.mols[k]
            cols = list(set(i.column() for i in items))
            cols = [c for c in cols if c > 1]  # never export the hidden ID or Structure via selection list
            cols.sort(key=self.table.horizontalHeader().visualIndex)
            t_heads = ["ID_HIDDEN", "Structure"] + [self.headers[c] for c in cols if c < len(self.headers)]
        f_filter = "SDF (*.sdf);;Molfile (*.mol);;SMILES (*.smi);;CSV (*.csv);;TDT (*.tdt);;PDB (*.pdb)"
        path, sel_f = QFileDialog.getSaveFileName(self, "Export Data", "", f_filter)
        if path:
            # Robustly infer the extension even if the Qt filter string is empty/unexpected.
            ext = ""
            m = re.search(r"\(([^)]+)\)", sel_f or "")
            if m:
                # e.g. "*.sdf" or "*.sdf *.mol" -> take first token
                tok = (m.group(1).split() or [""])[0]
                ext = tok.replace("*", "").strip()
            if not ext:
                # Fall back to what user typed.
                ext = (("." + path.split(".")[-1]) if "." in path else "")
            if not ext:
                # Last resort: default to .sdf
                ext = ".sdf"
            if not path.lower().endswith(ext.lower()):
                path += ext
            h_map = {h: i for i, h in enumerate(self.headers)}
            oids_list = list(t_mols.keys())
            row_cache = {oid: self.get_row_by_id(oid) for oid in oids_list}
            self._export_busy = True
            self._export_prep = {
                "path": path,
                "ext": ext,
                "t_mols": t_mols,
                "t_heads": t_heads,
                "h_map": h_map,
                "oids": oids_list,
                "rows": row_cache,
                "t_data": {},
                "idx": 0,
                "chunk": 48,
                "cols": [h for h in t_heads if h in self.headers],
            }
            self._on_tool_progress("Preparing export…", 0, max(len(oids_list), 1))
            QTimer.singleShot(0, self._export_snapshots_continue)

    def _export_snapshots_continue(self) -> None:
        prep = self._export_prep
        if not prep:
            return
        try:
            oids = prep["oids"]
            h_map = prep["h_map"]
            cols = prep["cols"]
            n = len(oids)
            chunk = prep["chunk"]
            start = prep["idx"]
            end = min(start + chunk, n)
            for j in range(start, end):
                oid = oids[j]
                r = prep["rows"].get(oid, -1)
                if r != -1:
                    prep["t_data"][oid] = {h: self._export_cell_text(r, h_map[h]) for h in cols}
                else:
                    prep["t_data"][oid] = {h: "" for h in cols}
            prep["idx"] = end
            self._on_tool_progress("Preparing export…", end, max(n, 1))
            if end < n:
                QTimer.singleShot(0, self._export_snapshots_continue)
                return
            path = prep["path"]
            ext = prep["ext"]
            t_data = prep["t_data"]
            t_mols = prep["t_mols"]
            t_heads = prep["t_heads"]
            self._export_prep = None
            self.process_queue.enqueue(
                f"Export to {path}",
                lambda ev, p=path, e=ext, m=t_mols, h=t_heads, d=t_data, s=self.signals: ExportWorker(
                    p, e, m, h, d, s, cancel_event=ev
                ),
            )
        except Exception as e:
            self._export_prep = None
            self._export_busy = False
            self._clear_tool_progress()
            QMessageBox.warning(self, "Export", str(e))
            self.status_label.setText("Ready")
