from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...permeability_prediction import PERMEABILITY_ENDPOINT_OPTIONS
from ...science_citations import permeability_dialog_footer_html
from ..qt_widget_utils import make_window_minimizable
from .scope import selection_scope_checked


class PermeabilityPredictorDialog(QDialog):
    """Predict Caco-2 / MDCK permeability and efflux endpoints (Chemprop GNN-MTL)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("Predict Permeability")
        self.setMinimumWidth(360)
        n_sel = len(parent._selected_logical_rows()) if parent is not None else 0
        self._have_selection = n_sel > 0

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        endpoints_box = QGroupBox("Cell lines / endpoints")
        ep_layout = QVBoxLayout(endpoints_box)
        ep_layout.setContentsMargins(8, 8, 8, 8)
        ep_layout.setSpacing(2)
        self._endpoint_cbs: dict[str, QCheckBox] = {}
        for column, label in PERMEABILITY_ENDPOINT_OPTIONS:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self._endpoint_cbs[column] = cb
            ep_layout.addWidget(cb)
        root.addWidget(endpoints_box)

        src_row = QHBoxLayout()
        src_row.setSpacing(6)
        src_row.addWidget(QLabel("Structure source:"))
        self.src_combo = QComboBox()
        self.src_combo.setMinimumWidth(160)
        src_row.addWidget(self.src_combo, 1)
        root.addLayout(src_row)

        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({n_sel} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        root.addWidget(self.only_selected_cb)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.predict_btn = QPushButton("Predict")
        self.predict_btn.clicked.connect(self._on_predict)
        btn_row.addWidget(self.predict_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        ref_lbl = QLabel(permeability_dialog_footer_html())
        ref_lbl.setWordWrap(True)
        ref_lbl.setTextFormat(Qt.RichText)
        ref_lbl.setOpenExternalLinks(True)
        ref_lbl.setStyleSheet("color: palette(mid);")
        root.addWidget(ref_lbl)

        if parent is not None:
            self.src_combo.addItems(parent.chemistry_tool_structure_sources())
        self.adjustSize()
        make_window_minimizable(self)

    def _selected_output_columns(self) -> list[str]:
        return [col for col, cb in self._endpoint_cbs.items() if cb.isChecked()]

    def _on_predict(self) -> None:
        if self.parent_app is None:
            return
        output_columns = self._selected_output_columns()
        if not output_columns:
            QMessageBox.warning(
                self,
                "Predict Permeability",
                "Select at least one cell line / endpoint.",
            )
            return
        only_selected = selection_scope_checked(self)
        allowed = self.parent_app._selected_oids_set() if only_selected else None
        if only_selected and not allowed:
            QMessageBox.warning(
                self,
                "Predict Permeability",
                "\u201cOnly selected rows\u201d is checked but nothing is selected.",
            )
            return
        src = self.src_combo.currentText()
        self.parent_app.schedule_permeability_prediction(
            src, only_selected=only_selected, output_columns=tuple(output_columns)
        )
        self.close()
