"""Settings dialog to change application-wide and table font sizes (live, theme-safe)."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..qt_widget_utils import make_window_minimizable
from ..theme import MAX_FONT_PT, MIN_FONT_PT, default_app_font_pt, default_table_font_pt


class FontSettingsDialog(QDialog):
    """Pick application-wide and table font sizes; emits preview signals while adjusting."""

    app_font_size_previewed = pyqtSignal(int)
    table_font_size_previewed = pyqtSignal(int)

    def __init__(self, current_app_pt: int, current_table_pt: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Font")
        self.setMinimumWidth(340)
        self._default_app_pt = default_app_font_pt()
        self._default_table_pt = default_table_font_pt()

        root = QVBoxLayout(self)
        form = QFormLayout()

        self._app_spin = self._make_spin(current_app_pt, self._default_app_pt)
        self._app_spin.valueChanged.connect(lambda pt: self.app_font_size_previewed.emit(int(pt)))
        form.addRow("Application font size:", self._app_spin)

        self._table_spin = self._make_spin(current_table_pt, self._default_table_pt)
        self._table_spin.valueChanged.connect(
            lambda pt: self.table_font_size_previewed.emit(int(pt))
        )
        form.addRow("Table font size:", self._table_spin)
        root.addLayout(form)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to Default")
        reset_btn.clicked.connect(self._reset_default)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        make_window_minimizable(self)

    @staticmethod
    def _make_spin(current_pt: int, default_pt: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(MIN_FONT_PT, MAX_FONT_PT)
        spin.setSuffix(" pt")
        spin.setValue(max(MIN_FONT_PT, min(MAX_FONT_PT, int(current_pt or default_pt))))
        return spin

    def _reset_default(self) -> None:
        self._app_spin.setValue(self._default_app_pt)
        self._table_spin.setValue(self._default_table_pt)

    def selected_app_point_size(self) -> int:
        return int(self._app_spin.value())

    def selected_table_point_size(self) -> int:
        return int(self._table_spin.value())
