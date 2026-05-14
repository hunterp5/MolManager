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
    QVBoxLayout,
    QWidget,
)

from rdkit import Chem

from ...utils import parse_molecule_from_cell_text
from ...workers import PKaPredictorSignals, PKaPredictorWorker
from .scope import selection_scope_checked


class PKaPredictorDialog(QDialog):
    """Predict microstate pKa values (pkasolver) from a structure column or a SMILES string."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("pKa Predictor")
        self.setMinimumWidth(320)
        n_sel = len(parent._selected_logical_rows()) if parent is not None else 0
        self._have_selection = n_sel > 0

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Table rows", "SMILES string"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_row.addWidget(QLabel("Input:"))
        mode_row.addWidget(self.mode_combo, 1)
        root.addLayout(mode_row)

        self._table_cfg = QWidget()
        tc_lyt = QVBoxLayout(self._table_cfg)
        tc_lyt.setContentsMargins(0, 0, 0, 0)
        tc_lyt.setSpacing(4)
        src_row = QHBoxLayout()
        src_row.setSpacing(6)
        src_row.addWidget(QLabel("Source:"))
        self.src_combo = QComboBox()
        self.src_combo.setMinimumWidth(160)
        src_row.addWidget(self.src_combo, 1)
        tc_lyt.addLayout(src_row)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({n_sel} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        tc_lyt.addWidget(self.only_selected_cb)
        root.addWidget(self._table_cfg)

        self._smiles_cfg = QWidget()
        sm_lyt = QVBoxLayout(self._smiles_cfg)
        sm_lyt.setContentsMargins(0, 0, 0, 0)
        sm_lyt.setSpacing(4)
        self.smiles_edit = QLineEdit()
        self.smiles_edit.setPlaceholderText("SMILES")
        sm_lyt.addWidget(self.smiles_edit)
        self._smiles_cfg.setVisible(False)
        root.addWidget(self._smiles_cfg)

        self.most_basic_only_cb = QCheckBox("Only calculate most basic pKa")
        self.most_basic_only_cb.setToolTip(
            "When checked, write a single value: the highest predicted pKa (strongest base / "
            "most basic ionization step). Otherwise all microstate pKas are listed."
        )
        self.most_basic_only_cb.toggled.connect(self._on_most_basic_toggled)
        root.addWidget(self.most_basic_only_cb)

        self.most_acidic_only_cb = QCheckBox("Only calculate most acidic pKa")
        self.most_acidic_only_cb.setToolTip(
            "When checked, write a single value: the lowest predicted pKa (strongest acid / "
            "most acidic ionization step)."
        )
        self.most_acidic_only_cb.toggled.connect(self._on_most_acidic_toggled)
        root.addWidget(self.most_acidic_only_cb)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.predict_btn = QPushButton("Predict")
        self.predict_btn.clicked.connect(self._on_predict)
        btn_row.addWidget(self.predict_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._pka_signals = PKaPredictorSignals(self.parent_app)
        self._pka_signals.finished.connect(self._on_batch_finished)
        self._pka_signals.failed.connect(self._on_failed)

        self._refresh_structure_sources()
        self.adjustSize()

    def _on_most_basic_toggled(self, on: bool) -> None:
        if on:
            self.most_acidic_only_cb.blockSignals(True)
            self.most_acidic_only_cb.setChecked(False)
            self.most_acidic_only_cb.blockSignals(False)

    def _on_most_acidic_toggled(self, on: bool) -> None:
        if on:
            self.most_basic_only_cb.blockSignals(True)
            self.most_basic_only_cb.setChecked(False)
            self.most_basic_only_cb.blockSignals(False)

    def _refresh_structure_sources(self) -> None:
        self.src_combo.clear()
        if self.parent_app is None:
            return
        candidates = ["Structure"] + self.parent_app._data_headers_confirmed_for_chemistry_tools()
        self.src_combo.addItems(candidates)

    def _on_mode_changed(self, idx: int) -> None:
        is_smiles = idx == 1
        self._table_cfg.setVisible(not is_smiles)
        self._smiles_cfg.setVisible(is_smiles)

    def _collect_table_mols(self, src: str, only_selected: bool) -> list[tuple[int, Chem.Mol]]:
        allowed = self.parent_app._selected_oids_set() if only_selected else None
        col = None if src == "Structure" else self.parent_app.headers.index(src)
        data: list[tuple[int, Chem.Mol]] = []
        for r in range(self.parent_app._table_model.rowCount()):
            t0 = self.parent_app._table_model.cell_text(r, 0)
            if not t0.isdigit():
                continue
            oid = int(t0)
            if allowed is not None and oid not in allowed:
                continue
            if src == "Structure":
                mol = self.parent_app.mols.get(oid)
                if mol is None:
                    mol = self.parent_app._mol_for_structure_row(r)
            else:
                if self.parent_app._table_model.is_pixmap_data_column(src):
                    mol = self.parent_app.mols.get(oid)
                    if mol is None:
                        raw = self.parent_app._table_model.backing_value_for_row_header(r, src)
                        mol = self.parent_app._mol_from_structure_text(raw) if raw else None
                    if mol is None:
                        mol = self.parent_app._mol_for_structure_row(r)
                else:
                    raw = self.parent_app._table_cell_text(r, col)
                    mol = self.parent_app._mol_from_structure_text(raw)
                if mol is not None:
                    self.parent_app.mols[oid] = mol
            if mol is not None:
                data.append((oid, mol))
        return data

    def _on_predict(self) -> None:
        if self.parent_app is None:
            return
        if self.mode_combo.currentIndex() == 1:
            smi = (self.smiles_edit.text() or "").strip()
            if not smi:
                QMessageBox.warning(self, "pKa Predictor", "Enter a SMILES string.")
                return
            mol = parse_molecule_from_cell_text(smi)
            if mol is None:
                QMessageBox.warning(self, "pKa Predictor", "Could not parse SMILES.")
                return
            rows: list[tuple[int | None, Chem.Mol | None]] = [(None, mol)]
        else:
            only_selected = selection_scope_checked(self)
            allowed = self.parent_app._selected_oids_set() if only_selected else None
            if only_selected and not allowed:
                QMessageBox.warning(
                    self,
                    "pKa Predictor",
                    "\u201cOnly selected rows\u201d is checked but nothing is selected.",
                )
                return
            src = self.src_combo.currentText()
            rows_m = self._collect_table_mols(src, only_selected)
            if not rows_m:
                QMessageBox.information(
                    self,
                    "pKa Predictor",
                    "No valid structures were found for this scope and source.",
                )
                return
            rows = list(rows_m)

        self.predict_btn.setEnabled(False)
        self.parent_app.status_label.setText("pKa prediction…")
        most_basic = bool(self.most_basic_only_cb.isChecked())
        most_acidic = bool(self.most_acidic_only_cb.isChecked())
        self.parent_app.process_queue.enqueue(
            f"pKa prediction ({len(rows)} molecules)",
            lambda ev, r=rows, ws=self.parent_app.signals, ps=self._pka_signals, mb=most_basic, ma=most_acidic: PKaPredictorWorker(
                r, ws, ps, cancel_event=ev, most_basic_only=mb, most_acidic_only=ma
            ),
        )

    def _on_batch_finished(self, results: list) -> None:
        self.predict_btn.setEnabled(True)
        table_rows = [(o, t) for o, t in results if o is not None]
        lone = [t for o, t in results if o is None]
        if table_rows:
            res = [(int(o), {"pKa": text}) for o, text in table_rows]
            self.parent_app.on_calc_finished(res, ["pKa"])
        if lone:
            QMessageBox.information(self, "pKa Predictor", lone[0])
        if not table_rows:
            self.parent_app._clear_tool_progress()
            self.parent_app.status_label.setText("Ready.")

    def _on_failed(self, msg: str) -> None:
        self.predict_btn.setEnabled(True)
        self.parent_app._clear_tool_progress()
        QMessageBox.warning(self, "pKa Predictor", msg or "Prediction failed.")
