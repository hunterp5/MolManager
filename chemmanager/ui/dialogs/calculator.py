from __future__ import annotations

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
)

from ..strings import TOOL_CALCULATOR
from .scope import selection_scope_checked


class CalculatorDialog(QDialog):
    def __init__(self, numeric_columns, selected_row_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle(TOOL_CALCULATOR)
        self.resize(550, 650)
        self._have_selection = selected_row_count > 0
        l = QVBoxLayout(self)
        v_group = QGroupBox("1. Variable Reference")
        v_lyt = QVBoxLayout(v_group)
        self.var_list = QListWidget()
        self.var_list.addItems(numeric_columns)
        v_lyt.addWidget(self.var_list)
        l.addWidget(v_group)
        c_group = QGroupBox("2. Calculation Logic")
        c_lyt = QVBoxLayout(c_group)
        self.expr_input = QLineEdit()
        self.var_list.itemDoubleClicked.connect(
            lambda i: self.expr_input.setText(self.expr_input.text() + f"[{i.text()}]")
        )
        c_lyt.addWidget(self.expr_input)
        c_lyt.addWidget(QLabel("<i>e.g. ([MW] / [TPSA]) * 100. Supports: sqrt, log10, exp...</i>"))
        l.addWidget(c_group)
        o_group = QGroupBox("3. Output Settings")
        o_lyt = QFormLayout(o_group)
        self.name_input = QLineEdit()
        o_lyt.addRow("Column Name:", self.name_input)
        l.addWidget(o_group)

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

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept_with_defaults)
        l.addWidget(bb)

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)

    def _accept_with_defaults(self):
        if not self.expr_input.text().strip():
            it = self.var_list.currentItem()
            if it is not None and it.text().strip():
                self.expr_input.setText(f"[{it.text().strip()}]")
        self.accept()
