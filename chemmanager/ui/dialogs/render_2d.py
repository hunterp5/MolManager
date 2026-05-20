from __future__ import annotations

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
)

from ..qt_widget_utils import make_window_minimizable
from ..strings import TOOL_RENDER_2D
from .scope import selection_scope_checked


class Render2DStructureDialog(QDialog):
    """Pick structure source and whether to render only selected table rows."""

    def __init__(self, candidates: list[str], selected_row_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(TOOL_RENDER_2D)
        self.setMinimumWidth(300)
        self.resize(360, 0)
        self._have_selection = selected_row_count > 0
        ly = QVBoxLayout(self)
        ly.setContentsMargins(10, 10, 10, 8)
        ly.setSpacing(8)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(4)
        form.setContentsMargins(0, 0, 0, 0)
        self.src_combo = QComboBox()
        self.src_combo.addItems(candidates)
        form.addRow("Draw from:", self.src_combo)
        ly.addLayout(form)
        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Selected rows only"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({selected_row_count})")
        else:
            self.only_selected_cb.setEnabled(False)
        ly.addWidget(self.only_selected_cb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        ly.addWidget(bb)
        self.adjustSize()
        make_window_minimizable(self)

    def chosen_source(self) -> str:
        return self.src_combo.currentText()

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)
