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
from rdkit import Chem

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


def _wildcard_symbol_to_smarts_token(symbol: str) -> str | None:
    """
    Map a sketcher element symbol to a SMARTS atom primitive.

    Use atomic numbers (``#6``) rather than organic-subset letters (``C``): in
    Daylight SMARTS, ``C``/``N``/``O`` match *aliphatic* atoms only and miss
    aromatic carbons/nitrogens in table molecules.
    """
    s = (symbol or "").strip()
    if not s:
        return None
    if s == "D":
        return "2#1"
    if s == "T":
        return "3#1"
    try:
        z = int(Chem.GetPeriodicTable().GetAtomicNumber(s))
    except Exception:
        return None
    if z <= 0:
        return None
    return f"#{z}"


def _wildcard_query_smarts(symbols: list[str], formal_charge: int = 0) -> str:
    """
    SMARTS atom query for a sketcher wildcard.

    Charge must be embedded in the SMARTS (``[#7,#8;+]``): ``SetFormalCharge`` on a
    QueryAtom is ignored by ``MolToSmarts``. Tokens use ``#Z`` so aromatic and
    aliphatic atoms both match.
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for sym in symbols or list(DEFAULT_WILDCARD_ELEMENTS):
        tok = _wildcard_symbol_to_smarts_token(str(sym))
        if tok and tok not in seen:
            seen.add(tok)
            tokens.append(tok)
    if not tokens:
        for sym in DEFAULT_WILDCARD_ELEMENTS:
            tok = _wildcard_symbol_to_smarts_token(sym)
            if tok and tok not in seen:
                seen.add(tok)
                tokens.append(tok)
    # Stable order by atomic number / isotope token for reproducible export.
    tokens.sort()
    body = ",".join(tokens) if tokens else "*"
    fc = int(formal_charge)
    if fc == 0:
        return f"[{body}]"
    if fc > 0:
        ch = f"+{fc}" if fc > 1 else "+"
    else:
        ch = str(fc) if fc < -1 else "-"
    return f"[{body};{ch}]"


class WildcardElementsDialog(QDialog):
    """Pick which elements a wildcard atom may match (SMARTS ``[#6,#7]``, …)."""

    def __init__(self, initial: list[str] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Wildcard elements")
        self.resize(360, 480)
        ly = QVBoxLayout(self)
        ly.addWidget(
            QLabel(
                "Select one or more elements this wildcard may represent.\n"
                "The sketch exports as SMARTS (e.g. [#6,#7]) for that position."
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
