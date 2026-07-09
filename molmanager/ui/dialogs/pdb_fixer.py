from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...workers.pdb_fixer import PdbFixerRequest, PdbFixerSignals, PdbFixerWorker
from ..qt_widget_utils import apply_monospace_to_text_edit, make_window_minimizable


class PdbFixerDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("Dock — Prepare PDB")
        self.setMinimumWidth(640)
        self.resize(700, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        io_gb = QGroupBox("Receptor PDB")
        io_form = QFormLayout(io_gb)

        self.edit_in = QLineEdit()
        self.edit_in.setPlaceholderText("receptor.pdb")
        br_in = QHBoxLayout()
        br_in.addWidget(self.edit_in, 1)
        btn_in = QPushButton("Browse…")
        btn_in.clicked.connect(self._browse_input)
        br_in.addWidget(btn_in)
        w_in = QWidget()
        w_in.setLayout(br_in)
        io_form.addRow("Input PDB:", w_in)

        self.edit_out = QLineEdit()
        self.edit_out.setPlaceholderText("receptor_prepared.pdb")
        br_out = QHBoxLayout()
        br_out.addWidget(self.edit_out, 1)
        btn_out = QPushButton("Browse…")
        btn_out.clicked.connect(self._browse_output)
        br_out.addWidget(btn_out)
        w_out = QWidget()
        w_out.setLayout(br_out)
        io_form.addRow("Output PDB:", w_out)
        root.addWidget(io_gb)

        opt_gb = QGroupBox("Preparation options")
        opt_form = QFormLayout(opt_gb)
        self.chk_remove_heterogens = QCheckBox("Remove heterogens (ligands, ions, buffers)")
        self.chk_remove_heterogens.setChecked(True)
        self.chk_remove_heterogens.toggled.connect(self._sync_water_enabled)
        opt_form.addRow(self.chk_remove_heterogens)

        self.chk_keep_water = QCheckBox("Keep crystallographic waters")
        self.chk_keep_water.setChecked(False)
        opt_form.addRow(self.chk_keep_water)

        self.chk_replace_nonstandard = QCheckBox("Replace non-standard residues (e.g. MSE → MET)")
        self.chk_replace_nonstandard.setChecked(True)
        opt_form.addRow(self.chk_replace_nonstandard)

        self.chk_add_missing_atoms = QCheckBox("Add missing heavy atoms in existing residues")
        self.chk_add_missing_atoms.setChecked(True)
        opt_form.addRow(self.chk_add_missing_atoms)

        self.chk_add_hydrogens = QCheckBox("Add missing hydrogens at pH")
        self.chk_add_hydrogens.setChecked(True)
        self.chk_add_hydrogens.toggled.connect(self._sync_ph_enabled)
        opt_form.addRow(self.chk_add_hydrogens)

        self.spin_ph = QDoubleSpinBox()
        self.spin_ph.setRange(0.0, 14.0)
        self.spin_ph.setDecimals(1)
        self.spin_ph.setSingleStep(0.1)
        self.spin_ph.setValue(7.0)
        opt_form.addRow("pH:", self.spin_ph)
        root.addWidget(opt_gb)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        apply_monospace_to_text_edit(self.log)
        root.addWidget(self.log, 1)

        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Prepare PDB")
        self.btn_run.clicked.connect(self._on_run)
        btn_row.addWidget(self.btn_run)
        btn_row.addStretch()
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

        self._signals = PdbFixerSignals(self)
        self._signals.finished.connect(self._on_finished)
        self._signals.failed.connect(self._on_failed)

        make_window_minimizable(self)
        self._sync_water_enabled()
        self._sync_ph_enabled()

    def _sync_water_enabled(self, *_args) -> None:
        self.chk_keep_water.setEnabled(self.chk_remove_heterogens.isChecked())

    def _sync_ph_enabled(self, *_args) -> None:
        self.spin_ph.setEnabled(self.chk_add_hydrogens.isChecked())

    def _suggest_output_path(self, input_path: str) -> None:
        path = Path(input_path)
        if not path.suffix:
            return
        suggested = path.with_name(f"{path.stem}_prepared{path.suffix}")
        if not (self.edit_out.text() or "").strip():
            self.edit_out.setText(str(suggested))

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Receptor PDB", "", "PDB (*.pdb);;All files (*.*)")
        if path:
            self.edit_in.setText(path)
            self._suggest_output_path(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Prepared receptor PDB", "", "PDB (*.pdb);;All files (*.*)")
        if path:
            self.edit_out.setText(path)

    def _append_log(self, text: str) -> None:
        t = (text or "").rstrip()
        if not t:
            return
        self.log.append(t)

    def _populate_open_prepare_paths(self, output_pdb: str) -> None:
        app = self.parent_app
        pdbqt = getattr(app, "_pdbqt_generator_dialog", None) if app is not None else None
        if pdbqt is None:
            return
        try:
            if output_pdb and hasattr(pdbqt, "edit_rec_pdb"):
                pdbqt.edit_rec_pdb.setText(output_pdb)
        except RuntimeError:
            pass

    def _on_run(self) -> None:
        app = self.parent_app
        in_path = (self.edit_in.text() or "").strip()
        out_path = (self.edit_out.text() or "").strip()
        if not in_path:
            QMessageBox.information(self, "Prepare PDB", "Select an input PDB file.")
            return
        if not out_path:
            QMessageBox.information(self, "Prepare PDB", "Set an output PDB path.")
            return
        if app is None:
            QMessageBox.warning(self, "Prepare PDB", "Main window not available.")
            return

        req = PdbFixerRequest(
            input_pdb_path=in_path,
            output_pdb_path=out_path,
            remove_heterogens=self.chk_remove_heterogens.isChecked(),
            keep_water=self.chk_keep_water.isChecked(),
            replace_nonstandard=self.chk_replace_nonstandard.isChecked(),
            add_missing_atoms=self.chk_add_missing_atoms.isChecked(),
            add_hydrogens=self.chk_add_hydrogens.isChecked(),
            ph=float(self.spin_ph.value()),
        )

        self.btn_run.setEnabled(False)
        self._append_log("Starting PDB preparation with PDBFixer…")
        app.process_queue.enqueue(
            "Prepare PDB",
            lambda ev, r=req, sig=self._signals: PdbFixerWorker(r, signals=sig, cancel_event=ev),
        )

    def _on_finished(self, output_pdb: str) -> None:
        self.btn_run.setEnabled(True)
        self._append_log(f"Prepared PDB written: {output_pdb}")
        self._populate_open_prepare_paths(output_pdb)

    def _on_failed(self, msg: str) -> None:
        self.btn_run.setEnabled(True)
        self._append_log(msg or "PDB preparation failed.")
