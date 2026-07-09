from __future__ import annotations

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
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

from rdkit import Chem

from ...workers.pdbqt_generator import PdbqtGenRequest, PdbqtGenSignals, PdbqtGeneratorWorker
from ..qt_widget_utils import apply_monospace_to_text_edit, make_window_minimizable


class PdbqtGeneratorDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("Dock — Prepare")
        self.setMinimumWidth(720)
        self.resize(760, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # Receptor
        rec_gb = QGroupBox("Receptor")
        rec_form = QFormLayout(rec_gb)
        self.edit_rec_pdb = QLineEdit()
        self.edit_rec_pdb.setPlaceholderText("receptor.pdb")
        br = QHBoxLayout()
        br.addWidget(self.edit_rec_pdb, 1)
        btn = QPushButton("Browse…")
        btn.clicked.connect(self._browse_rec_pdb)
        br.addWidget(btn)
        w = QWidget()
        w.setLayout(br)
        rec_form.addRow("Input PDB:", w)

        self.edit_rec_out = QLineEdit()
        self.edit_rec_out.setPlaceholderText("receptor.pdbqt")
        br2 = QHBoxLayout()
        br2.addWidget(self.edit_rec_out, 1)
        btn2 = QPushButton("Browse…")
        btn2.clicked.connect(self._browse_rec_out)
        br2.addWidget(btn2)
        w2 = QWidget()
        w2.setLayout(br2)
        rec_form.addRow("Output PDBQT:", w2)
        root.addWidget(rec_gb)

        # Ligand
        lig_gb = QGroupBox("Ligand")
        lig_form = QFormLayout(lig_gb)

        self.lig_mode = QComboBox()
        self.lig_mode.addItems(["SDF file", "SMILES strings", "Selected rows"])
        self.lig_mode.currentIndexChanged.connect(self._sync_mode_visibility)
        lig_form.addRow("Input mode:", self.lig_mode)

        self.edit_lig_sdf = QLineEdit()
        self.edit_lig_sdf.setPlaceholderText("ligands.sdf")
        bl = QHBoxLayout()
        bl.addWidget(self.edit_lig_sdf, 1)
        btnl = QPushButton("Browse…")
        btnl.clicked.connect(self._browse_lig_sdf)
        bl.addWidget(btnl)
        wl = QWidget()
        wl.setLayout(bl)
        lig_form.addRow("SDF:", wl)

        self.smiles_edit = QTextEdit()
        self.smiles_edit.setPlaceholderText("One SMILES per line")
        apply_monospace_to_text_edit(self.smiles_edit)
        lig_form.addRow("SMILES:", self.smiles_edit)

        self.src_combo = QComboBox()
        if self.parent_app is not None:
            self.src_combo.addItems(self.parent_app.chemistry_tool_structure_sources())
        lig_form.addRow("Rows source:", self.src_combo)

        self.edit_lig_out = QLineEdit()
        self.edit_lig_out.setPlaceholderText("ligand.pdbqt")
        blo = QHBoxLayout()
        blo.addWidget(self.edit_lig_out, 1)
        btnlo = QPushButton("Browse…")
        btnlo.clicked.connect(self._browse_lig_out)
        blo.addWidget(btnlo)
        wlo = QWidget()
        wlo.setLayout(blo)
        lig_form.addRow("Output PDBQT:", wlo)
        root.addWidget(lig_gb)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        apply_monospace_to_text_edit(self.log)
        root.addWidget(self.log, 1)

        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Generate .pdbqt")
        self.btn_run.clicked.connect(self._on_run)
        btn_row.addWidget(self.btn_run)
        btn_row.addStretch()
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

        self._signals = PdbqtGenSignals(self)
        self._signals.finished.connect(self._on_finished)
        self._signals.failed.connect(self._on_failed)

        make_window_minimizable(self)
        self._sync_mode_visibility()

    def _sync_mode_visibility(self, *_args) -> None:
        idx = self.lig_mode.currentIndex()
        is_sdf = idx == 0
        is_smiles = idx == 1
        is_rows = idx == 2
        self.edit_lig_sdf.setEnabled(is_sdf)
        self.smiles_edit.setEnabled(is_smiles)
        self.src_combo.setEnabled(is_rows)

    def _browse_rec_pdb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Receptor PDB", "", "PDB (*.pdb);;All files (*.*)")
        if path:
            self.edit_rec_pdb.setText(path)

    def _browse_rec_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Receptor PDBQT", "", "PDBQT (*.pdbqt);;All files (*.*)")
        if path:
            self.edit_rec_out.setText(path)

    def _browse_lig_sdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Ligand SDF", "", "SDF (*.sdf);;All files (*.*)")
        if path:
            self.edit_lig_sdf.setText(path)

    def _browse_lig_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Ligand PDBQT", "", "PDBQT (*.pdbqt);;All files (*.*)")
        if path:
            self.edit_lig_out.setText(path)

    def _append_log(self, text: str) -> None:
        t = (text or "").rstrip()
        if not t:
            return
        self.log.append(t)

    def _selected_rows_mols(self, src: str) -> list[tuple[int, Chem.Mol]]:
        app = self.parent_app
        if app is None:
            return []
        allowed = app._selected_oids_set()
        if not allowed:
            return []
        rows = app.collect_scoped_table_mols(src, only_selected=True)
        return [(int(oid), mol) for oid, mol in rows if mol is not None]

    def _on_run(self) -> None:
        app = self.parent_app
        ligand_mode = ("sdf", "smiles", "rows")[self.lig_mode.currentIndex()]
        ligand_rows = None
        ligand_smiles = None
        ligand_sdf = None

        if ligand_mode == "sdf":
            ligand_sdf = (self.edit_lig_sdf.text() or "").strip() or None
        elif ligand_mode == "smiles":
            ligand_smiles = [s.strip() for s in (self.smiles_edit.toPlainText() or "").splitlines() if s.strip()]
        else:
            src = self.src_combo.currentText()
            ligand_rows = self._selected_rows_mols(src)
            if not ligand_rows:
                QMessageBox.information(self, "Generate .pdbqt", "Select ligand rows in the table first.")
                return

        req = PdbqtGenRequest(
            receptor_pdb_path=(self.edit_rec_pdb.text() or "").strip() or None,
            receptor_pdbqt_out=(self.edit_rec_out.text() or "").strip() or None,
            ligand_mode=ligand_mode,
            ligand_sdf_path=ligand_sdf,
            ligand_smiles=ligand_smiles,
            ligand_rows=ligand_rows,
            ligand_pdbqt_out=(self.edit_lig_out.text() or "").strip() or None,
        )

        if not req.receptor_pdbqt_out and not req.ligand_pdbqt_out:
            QMessageBox.information(self, "Generate .pdbqt", "Set at least one output path (receptor or ligand).")
            return

        if app is None:
            QMessageBox.warning(self, "Generate .pdbqt", "Main window not available.")
            return

        self.btn_run.setEnabled(False)
        self._append_log("Starting PDBQT generation…")
        app.process_queue.enqueue(
            "Generate PDBQT",
            lambda ev, r=req, sig=self._signals: PdbqtGeneratorWorker(r, signals=sig, cancel_event=ev),
        )

    def _populate_open_smina_paths(self, receptor_pdbqt: str, ligand_pdbqt: str) -> None:
        """If the Smina dialog is open, fill receptor/ligand path fields with generated PDBQT files."""
        app = self.parent_app
        dock = getattr(app, "_smina_dock_dialog", None) if app is not None else None
        if dock is None:
            return
        try:
            if receptor_pdbqt and hasattr(dock, "edit_receptor"):
                dock.edit_receptor.setText(receptor_pdbqt)
            if ligand_pdbqt and hasattr(dock, "edit_ligand"):
                dock.edit_ligand.setText(ligand_pdbqt)
        except RuntimeError:
            pass

    def _on_finished(self, receptor_pdbqt: str, ligand_pdbqt: str) -> None:
        self.btn_run.setEnabled(True)
        if receptor_pdbqt:
            self._append_log(f"Receptor PDBQT written: {receptor_pdbqt}")
        if ligand_pdbqt:
            self._append_log(f"Ligand PDBQT written: {ligand_pdbqt}")
        self._populate_open_smina_paths(receptor_pdbqt, ligand_pdbqt)

    def _on_failed(self, msg: str) -> None:
        self.btn_run.setEnabled(True)
        self._append_log(msg or "PDBQT generation failed.")

