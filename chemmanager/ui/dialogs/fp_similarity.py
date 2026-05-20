from __future__ import annotations

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


from ...utils import parse_molecule_from_cell_text, safe_float
from ...workers import FPSimilaritySignals, FPSimilarityWorker, SIMILARITY_FP_TYPE_LABELS, fingerprint_bitvect_for_ui_choice
from ..strings import COLUMN_TANIMOTO_SIMILARITY
from ..qt_widget_utils import make_window_minimizable
from .scope import selection_scope_checked


class FPSimilarityDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("Fingerprint Similarity")
        self.resize(700, 500)
        n_sel = len(parent._selected_logical_rows()) if parent is not None else 0
        self._have_selection = n_sel > 0
        layout = QVBoxLayout(self)

        ctrl = QHBoxLayout()
        layout.addLayout(ctrl)
        ctrl.addWidget(QLabel("Fingerprint:"))
        self.fp_combo = QComboBox()
        self.fp_combo.addItems(SIMILARITY_FP_TYPE_LABELS)
        ctrl.addWidget(self.fp_combo)

        ctrl.addWidget(QLabel("Query Source:"))
        self.query_combo = QComboBox()
        self.query_combo.addItems(["From Table Row", "SMILES Input"])
        ctrl.addWidget(self.query_combo)
        self.row_select = QComboBox()
        ctrl.addWidget(self.row_select)
        self.smi_input = QLineEdit()
        self.smi_input.setPlaceholderText("Enter SMILES")
        self.smi_input.setVisible(False)
        ctrl.addWidget(self.smi_input)

        self.compute_btn = QPushButton("Compute")
        self.compute_btn.clicked.connect(self.compute)
        ctrl.addWidget(self.compute_btn)

        self._fp_sim_signals = FPSimilaritySignals(self.parent_app)
        self._fp_sim_signals.finished.connect(self._on_fp_similarity_finished)
        self._fp_sim_signals.failed.connect(self._on_fp_similarity_failed)

        self.query_combo.currentIndexChanged.connect(self._toggle_query_mode)

        self.only_selected_cb = QCheckBox("Only compare to selected rows")
        self._only_selected_scope_prefix = "Only compare to selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({n_sel} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        layout.addWidget(self.only_selected_cb)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(3)
        self.results_table.setHorizontalHeaderLabels(["OID", "Similarity", "SMILES"])
        self.results_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.results_table)

        btn_row = QHBoxLayout()
        layout.addLayout(btn_row)
        self.add_btn = QPushButton("Add Selected to Table")
        self.add_btn.clicked.connect(self.add_selected_to_main)
        btn_row.addWidget(self.add_btn)
        btn_row.addStretch()

        self._refresh_row_list()
        make_window_minimizable(self)

    def _toggle_query_mode(self, idx):
        if idx == 0:
            self.row_select.setVisible(True)
            self.smi_input.setVisible(False)
        else:
            self.row_select.setVisible(False)
            self.smi_input.setVisible(True)

    def _refresh_row_list(self):
        self.row_select.clear()
        m = self.parent_app._table_model
        seen: set[int] = set()
        ordered: list[str] = []
        for r in range(m.rowCount()):
            t0 = m.cell_text(r, 0)
            if not t0.isdigit():
                continue
            oid = int(t0)
            if oid in seen:
                continue
            seen.add(oid)
            ordered.append(str(oid))
        self.row_select.addItems(ordered)

    def compute(self):
        fp_choice = self.fp_combo.currentText()
        if self.query_combo.currentIndex() == 0:
            qid_text = self.row_select.currentText()
            if not qid_text:
                return
            qid = int(qid_text)
            r = self.parent_app.get_row_by_id(qid)
            qmol = self.parent_app._mol_for_structure_row(r) if r >= 0 else None
        else:
            smi = self.smi_input.text().strip()
            if not smi:
                return
            qmol = parse_molecule_from_cell_text(smi)

        if qmol is None:
            QMessageBox.warning(self, "Query Error", "Could not parse query molecule.")
            return

        qfp = fingerprint_bitvect_for_ui_choice(qmol, fp_choice)
        if qfp is None:
            QMessageBox.warning(self, "Fingerprint Error", "Could not compute query fingerprint.")
            return

        only_sel = selection_scope_checked(self)
        allowed = self.parent_app._selected_oids_set() if only_sel else None
        if only_sel and not allowed:
            QMessageBox.warning(
                self,
                "Fingerprint Similarity",
                "“Only compare to selected rows” is checked but nothing is selected.",
            )
            return

        targets = []
        m = self.parent_app._table_model
        for r in range(m.rowCount()):
            oid = m.row_oid(r)
            if allowed is not None and oid not in allowed:
                continue
            mol = self.parent_app._mol_for_structure_row(r)
            if mol is not None:
                targets.append((oid, mol))

        self.compute_btn.setEnabled(False)
        self.parent_app.process_queue.enqueue_fast(
            "Fingerprint similarity",
            lambda ev, q=qfp, t=targets, c=fp_choice, sig=self._fp_sim_signals: FPSimilarityWorker(
                q, t, c, sig, cancel_event=ev
            ),
        )

    def _on_fp_similarity_finished(self, rows):
        self.compute_btn.setEnabled(True)
        self.results_table.setRowCount(0)
        ordered = sorted(rows or [], key=lambda x: x[1], reverse=True)
        for oid, sim, smi in ordered:
            r = self.results_table.rowCount()
            self.results_table.insertRow(r)
            self.results_table.setItem(r, 0, QTableWidgetItem(str(oid)))
            self.results_table.setItem(r, 1, QTableWidgetItem(f"{sim:.4f}"))
            self.results_table.setItem(r, 2, QTableWidgetItem(smi))

    def _on_fp_similarity_failed(self, msg: str):
        self.compute_btn.setEnabled(True)
        QMessageBox.warning(self, "Fingerprint Similarity", msg or "Computation failed.")

    def add_selected_to_main(self):
        rows = set(i.row() for i in self.results_table.selectedItems())
        if not rows:
            return
        base = COLUMN_TANIMOTO_SIMILARITY
        name = base
        cnt = 1
        while name in self.parent_app.headers:
            cnt += 1
            name = f"{base} ({cnt})"
        m = self.parent_app._table_model
        nc = m.columnCount()
        self.parent_app.headers.append(name)
        m.insert_column_at(nc, name, None)

        sel_map: dict[int, str] = {}
        for r in rows:
            try:
                oid = int(self.results_table.item(r, 0).text())
                sim = safe_float(self.results_table.item(r, 1).text())
                if sim is not None:
                    sel_map[oid] = f"{sim:.4f}"
            except Exception:
                continue

        try:
            self.parent_app.table.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            m.fill_column_from_oid_map(name, sel_map, default="N/A")
            self.parent_app._sync_global_bounds_for_headers([name], refresh_filters=True)
        finally:
            try:
                self.parent_app.table.setUpdatesEnabled(True)
            except Exception:
                pass
        self.parent_app.status_label.setText(f"Added similarity column '{name}' to table")
