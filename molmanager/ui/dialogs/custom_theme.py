"""Custom GUI theme color picker (Settings → GUI → Custom)."""

from __future__ import annotations

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..qt_widget_utils import make_window_minimizable
from ..theme import (
    CUSTOM_PALETTE_ROLES,
    default_custom_palette_colors,
    load_saved_custom_palette_colors,
)


class CustomThemeDialog(QDialog):
    """Pick and preview palette role colors for the Custom GUI theme."""

    def __init__(self, parent=None, *, initial_colors: dict[str, str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Custom Theme Colors")
        self.setMinimumWidth(420)
        self.resize(460, 0)
        make_window_minimizable(self)

        colors = dict(default_custom_palette_colors())
        colors.update(load_saved_custom_palette_colors())
        if initial_colors:
            for k, v in initial_colors.items():
                c = QColor(str(v))
                if c.isValid():
                    colors[k] = c.name()
        self._colors = colors
        self._buttons: dict[str, QPushButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        tip = QLabel(
            "Choose colors for the Custom theme. They are saved with your GUI settings "
            "and applied when Custom Mode is selected."
        )
        tip.setWordWrap(True)
        root.addWidget(tip)

        form = QFormLayout()
        form.setSpacing(6)
        for key, label, _role in CUSTOM_PALETTE_ROLES:
            row = QHBoxLayout()
            swatch = QPushButton()
            swatch.setFixedSize(72, 24)
            swatch.setCursor(swatch.cursor())
            swatch.setToolTip(f"Pick color for {label}")
            swatch.clicked.connect(lambda *_a, k=key: self._pick(k))
            self._buttons[key] = swatch
            row.addWidget(swatch)
            row.addStretch(1)
            form.addRow(f"{label}:", row)
        root.addLayout(form)

        reset_btn = QPushButton("Reset to Light defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        root.addWidget(reset_btn)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self._refresh_buttons()

    def selected_colors(self) -> dict[str, str]:
        return {k: str(v) for k, v in self._colors.items()}

    def _refresh_buttons(self) -> None:
        for key, btn in self._buttons.items():
            c = QColor(self._colors.get(key, "#888888"))
            btn.setStyleSheet(
                f"background-color: {c.name()}; border: 1px solid #666; border-radius: 3px;"
            )
            btn.setText(c.name())

    def _pick(self, key: str) -> None:
        start = QColor(self._colors.get(key, "#888888"))
        chosen = QColorDialog.getColor(start, self, f"Pick {key.replace('_', ' ')}")
        if not chosen.isValid():
            return
        self._colors[key] = chosen.name()
        self._refresh_buttons()

    def _reset_defaults(self) -> None:
        self._colors = default_custom_palette_colors()
        self._refresh_buttons()
