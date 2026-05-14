"""PubChem querying and medchem summary via PubChemPy."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import QObject, QRunnable, Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QShortcut,
    QTextEdit,
    QGroupBox,
    QVBoxLayout,
)

from ..qt_widget_utils import apply_monospace_to_text_edit
from ..threadpool_access import start_runnable_on_app_pool


@dataclass(frozen=True)
class PubChemResult:
    cid: int | None
    smiles: str
    fields: dict[str, str]


def _get_pubchem_compound_by_smiles(smiles: str):
    try:
        import pubchempy as pcp
    except Exception as e:
        raise RuntimeError("PubChemPy is required. Install requirements.txt.") from e

    comps = pcp.get_compounds(smiles, namespace="smiles")
    return comps[0] if comps else None


class _QuerySignals(QObject):
    progress = pyqtSignal(int, int, str)  # done, total, current_smiles
    finished = pyqtSignal(list, list)  # results, logs (strings)


class _PubChemBatchWorker(QRunnable):
    def __init__(self, smiles_list: list[str], *, selected_fields: list[str]):
        super().__init__()
        self.smiles_list = smiles_list
        self.selected_fields = selected_fields
        self.signals = _QuerySignals()
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        results: list[PubChemResult] = []
        logs: list[str] = []
        total = len(self.smiles_list)
        done = 0
        for smi in self.smiles_list:
            if self._cancel:
                logs.append("Cancelled by user.")
                break
            done += 1
            self.signals.progress.emit(done, total, smi)
            try:
                comp = _get_pubchem_compound_by_smiles(smi)
            except Exception as e:
                logs.append(f"{smi} | ERROR: {e}")
                continue
            if comp is None:
                logs.append(f"{smi} | No PubChem result")
                continue
            fields = _extract_medchem_fields(comp, selected=self.selected_fields)
            cid = getattr(comp, "cid", None)
            results.append(PubChemResult(cid=int(cid) if cid is not None else None, smiles=smi, fields=fields))
            logs.append(f"{smi} | OK | CID={cid}")
        self.signals.finished.emit(results, logs)


_PUBCHEM_FIELD_DEFS: list[tuple[str, str, str, str]] = [
    # (field_key, label, pubchempy attribute, group)
    ("CID", "CID", "cid", "Identifiers / names"),
    ("IUPAC", "IUPAC name", "iupac_name", "Identifiers / names"),
    ("InChIKey", "InChIKey", "inchikey", "Identifiers / names"),

    # PubChemPy: canonical_smiles / isomeric_smiles deprecated → connectivity_smiles / smiles
    ("CanonicalSMILES", "Connectivity SMILES (canonical)", "connectivity_smiles", "Structures"),
    ("IsomericSMILES", "Isomeric SMILES", "smiles", "Structures"),
    ("MolecularFormula", "Molecular formula", "molecular_formula", "Structures"),

    ("MolecularWeight", "Molecular weight", "molecular_weight", "PhysChem"),
    ("XlogP", "XlogP", "xlogp", "PhysChem"),
    ("TPSA", "tPSA", "tpsa", "PhysChem"),
    ("Charge", "Formal charge", "charge", "PhysChem"),

    ("HBD", "H-bond donors (HBD)", "hbond_donor_count", "Counts / topology"),
    ("HBA", "H-bond acceptors (HBA)", "hbond_acceptor_count", "Counts / topology"),
    ("RotBonds", "Rotatable bonds", "rotatable_bond_count", "Counts / topology"),
    ("HeavyAtomCount", "Heavy atom count", "heavy_atom_count", "Counts / topology"),
    ("RingCount", "Ring count", "ring_count", "Counts / topology"),
    ("Complexity", "Complexity", "complexity", "Counts / topology"),
]


def _extract_medchem_fields(comp, *, selected: list[str] | None = None) -> dict[str, str]:
    # PubChemPy Compound objects expose many attributes; extract only what the user requested.
    def g(name: str) -> str:
        v = getattr(comp, name, None)
        if v is None:
            return ""
        if isinstance(v, (list, tuple)):
            return ", ".join(str(x) for x in v[:20])
        return str(v)

    out: dict[str, str] = {}
    selected_set = set(selected) if selected else {k for (k, _, _, _) in _PUBCHEM_FIELD_DEFS}
    for key, _label, attr, _group in _PUBCHEM_FIELD_DEFS:
        if key not in selected_set:
            continue
        out[key] = g(attr)
    return {k: v for k, v in out.items() if v != ""}


class PubChemDialog(QDialog):
    def __init__(self, parent=None, *, initial_smiles: list[str] | None = None, auto_query: bool = False):
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("External — PubChem query")
        self.resize(820, 560)
        self._last: list[PubChemResult] = []
        self._worker: _PubChemBatchWorker | None = None

        root = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("SMILES:"))
        self.smiles = QLineEdit()
        self.smiles.setPlaceholderText("Paste SMILES here, or use the Sketcher button…")
        row.addWidget(self.smiles, 1)
        self.btn_query = QPushButton("Query")
        self.btn_query.setToolTip("Run PubChem lookup for the SMILES above, or for selected rows if “Only Query Selected” is checked.")
        self.btn_query.clicked.connect(self._run_query)
        row.addWidget(self.btn_query)
        self.chk_only_selected = QCheckBox("Only Query Selected")
        self.chk_only_selected.setToolTip(
            "When checked, Query uses SMILES from selected rows in the main table (ignores the text box)."
        )
        row.addWidget(self.chk_only_selected)
        self.btn_sketch = QPushButton("Sketcher…")
        self.btn_sketch.clicked.connect(self._use_sketcher)
        row.addWidget(self.btn_sketch)
        root.addLayout(row)

        self.status = QLabel("")
        self.status.setStyleSheet("color: palette(mid);")
        root.addWidget(self.status)

        gb_fields = QGroupBox("Retrieve fields")
        fields_v = QVBoxLayout(gb_fields)
        self.field_checks: dict[str, QCheckBox] = {}
        groups: dict[str, list[tuple[str, str, str, str]]] = {}
        for item in _PUBCHEM_FIELD_DEFS:
            groups.setdefault(item[3], []).append(item)

        for group_name in ["Identifiers / names", "Structures", "PhysChem", "Counts / topology"]:
            items = groups.get(group_name, [])
            if not items:
                continue
            gb = QGroupBox(group_name)
            g = QGridLayout(gb)
            for i, (key, label, _attr, _grp) in enumerate(items):
                cb = QCheckBox(f"{label} ({key})")
                cb.setChecked(True)
                self.field_checks[key] = cb
                g.addWidget(cb, i // 2, i % 2)
            fields_v.addWidget(gb)

        root.addWidget(gb_fields)

        self.out = QTextEdit()
        self.out.setReadOnly(True)
        apply_monospace_to_text_edit(self.out)
        root.addWidget(self.out, 1)

        bottom = QHBoxLayout()
        self.btn_add = QPushButton("Add result to table")
        self.btn_add.setEnabled(False)
        self.btn_add.clicked.connect(self._add_to_table)
        bottom.addWidget(self.btn_add)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_query)
        bottom.addWidget(self.btn_cancel)
        bottom.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._run_query)
        if initial_smiles:
            self.set_smiles_list(initial_smiles)
            if auto_query:
                self._run_query()

    def set_smiles_list(self, smiles_list: list[str]) -> None:
        smiles_list = [s.strip() for s in (smiles_list or []) if s and s.strip()]
        self.smiles.setText("\n".join(smiles_list))

    def _parse_smiles_inputs(self) -> list[str]:
        # Accept multiple SMILES separated by newlines/semicolons/commas.
        # Also accept multiple SMILES concatenated by periods, per app workflow.
        raw = (self.smiles.text() or "").strip()
        if not raw:
            return []
        tmp = raw.replace(";", "\n").replace(",", "\n").replace(".", "\n")
        parts = [p.strip() for p in tmp.splitlines() if p.strip()]
        # De-duplicate while preserving order
        out: list[str] = []
        seen = set()
        for p in parts:
            if p not in seen:
                out.append(p)
                seen.add(p)
        return out

    def _use_sketcher(self) -> None:
        # Reuse the app's existing SketcherDialog (but we need the SMILES from its canvas).
        try:
            from ..sketcher import SketcherDialog
        except Exception as e:
            QMessageBox.warning(self, "PubChem", str(e))
            return

        dlg = SketcherDialog(self.parent_app if self.parent_app is not None else self)
        dlg.setModal(True)
        dlg.setWindowModality(Qt.ApplicationModal)
        if dlg.exec_() != QDialog.Accepted:
            # Even on cancel/close, user may have drawn something; try to capture it.
            pass

        try:
            parts = dlg.canvas.fragment_smiles_parts()
        except Exception:
            parts = []
        if not parts:
            QMessageBox.information(self, "PubChem", "No valid SMILES could be exported from the sketch.")
            return
        # Load fragments into the SMILES field; user runs Query when ready.
        self.chk_only_selected.setChecked(False)
        self.smiles.setText("\n".join(parts))
        self.status.setText("SMILES loaded from sketcher — press Query when ready.")

    def _run_query(self) -> None:
        if self.chk_only_selected.isChecked():
            app = self.parent_app
            if app is None:
                QMessageBox.information(self, "PubChem", "Main application is not available.")
                return
            try:
                smiles_list = app._selected_smiles_strings()
            except Exception:
                smiles_list = []
            if not smiles_list:
                QMessageBox.information(
                    self, "PubChem", "Select one or more rows that have a SMILES value first."
                )
                return
        else:
            smiles_list = self._parse_smiles_inputs()
            if not smiles_list:
                QMessageBox.information(self, "PubChem", "Enter at least one SMILES string first.")
                return
        selected_fields = [k for k, cb in self.field_checks.items() if cb.isChecked()]
        if not selected_fields:
            QMessageBox.information(self, "PubChem", "Select at least one field to retrieve.")
            return
        # Run in background so the UI remains responsive for large batches.
        self._last = []
        self.btn_add.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_query.setEnabled(False)
        self.chk_only_selected.setEnabled(False)
        self.btn_sketch.setEnabled(False)
        self.status.setText(f"Starting PubChem queries… (N={len(smiles_list)})")
        self.out.setPlainText("")

        self._worker = _PubChemBatchWorker(smiles_list, selected_fields=selected_fields)
        self._worker.signals.progress.connect(self._on_progress)
        self._worker.signals.finished.connect(self._on_finished)
        start_runnable_on_app_pool(self.parent_app, self._worker)

    def _cancel_query(self) -> None:
        w = getattr(self, "_worker", None)
        if w is not None:
            try:
                w.cancel()
            except Exception:
                pass
        self.status.setText("Cancelling…")

    def _on_progress(self, done: int, total: int, smi: str) -> None:
        self.status.setText(f"Querying PubChem… {done}/{total}  ({smi})")

    def _on_finished(self, results: list, logs: list) -> None:
        self._last = list(results or [])
        self.btn_add.setEnabled(bool(self._last))
        self.btn_cancel.setEnabled(False)
        self.btn_query.setEnabled(True)
        self.chk_only_selected.setEnabled(True)
        self.btn_sketch.setEnabled(True)
        self.status.setText(f"Done. Success: {len(self._last)}  Total: {len(logs)}")
        # Display a compact log by default (big batches stay readable).
        self.out.setPlainText("\n".join(str(x) for x in (logs or [])))
        self._worker = None

    def _add_to_table(self) -> None:
        if self.parent_app is None or not self._last:
            return
        added = 0
        for r in self._last:
            try:
                self.parent_app.add_row_from_external_record(r.smiles, r.fields)
                added += 1
            except Exception:
                continue
        app = self.parent_app
        if hasattr(app, "status_label"):
            if added:
                app.status_label.setText(f"PubChem: added {added} row(s) to the table.")
            else:
                app.status_label.setText("PubChem: no rows were added (see log in this window for errors).")

