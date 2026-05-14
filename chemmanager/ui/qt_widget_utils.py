"""Small Qt widget helpers shared across dialogs (keep dependency-free beyond PyQt5)."""

from __future__ import annotations

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QTextEdit


def monospace_text_font() -> QFont:
    f = QFont("Consolas")
    if not f.exactMatch():
        f = QFont("Courier New")
    f.setStyleHint(QFont.Monospace)
    return f


def apply_monospace_to_text_edit(w: QTextEdit) -> None:
    w.setFont(monospace_text_font())
