"""Wildcard atom helpers and element-picker dialog."""

from typing import Any

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..qt_widget_utils import make_window_minimizable
from .constants import (
    DEFAULT_WILDCARD_ELEMENTS,
    WILDCARD_ELEMENT,
    WILDCARD_ELEMENT_CHOICES,
)


def _is_wildcard_node(n: dict[str, Any]) -> bool:
    return n.get("element") == WILDCARD_ELEMENT


def _normalize_wildcard_elements(n: dict[str, Any]) -> list[str]:
    raw = n.get("wildcard_els")
    if not raw:
        return list(DEFAULT_WILDCARD_ELEMENTS)
    out: list[str] = []
    for x in raw:
        s = str(x).strip()
        if s in WILDCARD_ELEMENT_CHOICES and s not in out:
            out.append(s)
    return out or list(DEFAULT_WILDCARD_ELEMENTS)


def _wildcard_query_smarts(symbols: list[str]) -> str:
    syms = sorted(set(symbols))
    if not syms:
        syms = list(DEFAULT_WILDCARD_ELEMENTS)
    return f"[{','.join(syms)}]"


class WildcardElementsDialog(QDialog):
    """Pick which elements a wildcard atom may match (SMARTS `[El1,El2,...]`)."""

    def __init__(self, initial: list[str] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Wildcard elements")
        self.resize(360, 480)
        ly = QVBoxLayout(self)
        ly.addWidget(
            QLabel(
                "Select one or more elements this wildcard may represent.\n"
                "The sketch exports as SMARTS (e.g. [C,N]) for that position."
            )
        )
        self._checks: dict[str, QCheckBox] = {}
        sel = set(initial or DEFAULT_WILDCARD_ELEMENTS)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(340)
        inner = QWidget()
        grid = QGridLayout(inner)
        for i, sym in enumerate(WILDCARD_ELEMENT_CHOICES):
            cb = QCheckBox(sym)
            cb.setChecked(sym in sel)
            self._checks[sym] = cb
            grid.addWidget(cb, i // 2, i % 2)
        scroll.setWidget(inner)
        ly.addWidget(scroll)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        ly.addWidget(bb)
        make_window_minimizable(self)

    def selected_elements(self) -> list[str]:
        return [s for s in WILDCARD_ELEMENT_CHOICES if self._checks[s].isChecked()]
