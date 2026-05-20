from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...science_citations import descriptor_dialog_footer_html
from ..qt_widget_utils import make_window_minimizable
from .scope import selection_scope_checked


class PropertyDialog(QDialog):
    def __init__(self, columns, selected_row_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calculate Descriptors")
        self.resize(600, 700)
        self._have_selection = selected_row_count > 0
        l = QVBoxLayout(self)

        src_box = QGroupBox("1. Input Configuration")
        s_lyt = QVBoxLayout(src_box)
        self.src_combo = QComboBox()
        self.src_combo.addItems([c for c in columns if (c or "").strip()])
        s_lyt.addWidget(self.src_combo)
        l.addWidget(src_box)

        scope_box = QGroupBox("Scope")
        scope_lyt = QVBoxLayout(scope_box)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({selected_row_count} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        scope_lyt.addWidget(self.only_selected_cb)
        l.addWidget(scope_box)

        desc_box = QGroupBox("2. Descriptor Categories")
        d_box_lyt = QVBoxLayout(desc_box)
        ref_lbl = QLabel(descriptor_dialog_footer_html())
        ref_lbl.setWordWrap(True)
        ref_lbl.setTextFormat(Qt.RichText)
        ref_lbl.setOpenExternalLinks(True)
        ref_lbl.setStyleSheet("color: palette(mid);")
        d_box_lyt.addWidget(ref_lbl)
        self.tabs = QTabWidget()

        categories = {
            "Drug-likeness": {
                "SMILES String": "SMILES",
                "InChI Key": "INCHIKEY",
                "Molecular formula": "MOLFORMULA",
                "QED Score": "QED",
                "Mol Weight": "MolWt",
                "LogP": "MolLogP",
                "LogD 7.4": "LOGD74",
                "LogS intrinsic (ESOL)": "LOGS_ESOL",
                "LogS 7.4": "LOGS74",
                "TPSA": "TPSA",
                "Ro5 violations": "RO5_VIOLATIONS",
                "Ro5 pass": "RO5_PASS",
                "CNS MPO score": "CNS_MPO",
                "Molar Refractivity": "MolMR",
                "Fraction CSP3": "FractionCSP3",
                "Labute ASA": "LabuteASA",
            },
            "Structural Counts": {
                "Heavy Atoms": "HeavyAtomCount",
                "NH/OH Count": "NumNHOH",
                "NO Count": "NumNO",
                "Heteroatoms": "NumHeteroatoms",
                "H-Bond Donors": "NumHDonors",
                "H-Bond Acceptors": "NumHAcceptors",
                "Rotatable Bonds": "NumRotatableBonds",
                "Valence Electrons": "NumValenceElectrons",
            },
            "Ring Systems": {
                "Total Rings": "RingCount",
                "Aromatic Rings": "NumAromaticRings",
                "Saturated Rings": "NumSaturatedRings",
                "Aliphatic Rings": "NumAliphaticRings",
                "Spiro Atoms": "NumSpiroAtoms",
                "Bridgehead Atoms": "NumBridgeheadAtoms",
            },
            "Atom Counts": {
                "Carbons": "Count_C",
                "Nitrogens": "Count_N",
                "Oxygens": "Count_O",
                "Fluorines": "Count_F",
                "Chlorines": "Count_Cl",
                "Bromines": "Count_Br",
                "Iodines": "Count_I",
                "Sulfurs": "Count_S",
                "Phosphorus": "Count_P",
            },
            "Complexity": {
                "Bertz Complexity": "BertzCT",
                "Balaban J": "BalabanJ",
                "Hall-Kier Alpha": "HallKierAlpha",
                "Kappa 1": "Kappa1",
                "Kappa 2": "Kappa2",
            },
            "Electronic": {
                "Max Partial Charge": "MaxPartialCharge",
                "Min Partial Charge": "MinPartialCharge",
                "Max Abs Partial Charge": "MaxAbsPartialCharge",
                "Min Abs Partial Charge": "MinAbsPartialCharge",
            },
        }

        categories["Fingerprints"] = {
            "2D pharmacophore (Gobbi) on-bits": "FP_Pharm2D_Gobbi",
            "MACCS (166) bits": "FP_MACCS_166",
            "Morgan (r=2,1024) bits": "FP_Morgan_2_1024",
            "RDKit path FP (2048 bits)": "FP_RDK_2048",
            "RDKit path FP (4096 bits)": "FP_RDK_4096",
        }

        self.cbs = {}
        for cat_name in sorted(categories, key=str.casefold):
            props = categories[cat_name]
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            tab = QWidget()
            tab_lyt = QVBoxLayout(tab)
            for disp, internal in sorted(props.items(), key=lambda kv: kv[0].casefold()):
                cb = QCheckBox(disp)
                tab_lyt.addWidget(cb)
                self.cbs[disp] = (cb, internal)
            tab_lyt.addStretch()
            scroll.setWidget(tab)
            self.tabs.addTab(scroll, cat_name)

        d_box_lyt.addWidget(self.tabs)
        l.addWidget(desc_box)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        l.addWidget(bb)
        make_window_minimizable(self)

    def get_selected(self):
        sel_disp, sel_int = [], []
        for d, (cb, i) in self.cbs.items():
            if cb.isChecked():
                sel_disp.append(d)
                sel_int.append(i)
        return sel_disp, sel_int

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)
