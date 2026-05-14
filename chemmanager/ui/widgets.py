"""Small reusable widgets; filter cards live in :mod:`chemmanager.ui.filters.cards`."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QTableWidgetItem

from ..utils import safe_float

from .filters.cards import (
    CategoryFilterCard,
    FilterCard,
    SubstructureFilterCard,
    TextFilterCard,
    style_filter_card_remove_button,
)

__all__ = [
    "CategoryFilterCard",
    "FilterCard",
    "NumericTableWidgetItem",
    "SubstructureFilterCard",
    "TextFilterCard",
    "style_filter_card_remove_button",
]


class NumericTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        val = safe_float(self.data(Qt.EditRole))
        other_val = safe_float(other.data(Qt.EditRole))
        if val is None:
            return True
        if other_val is None:
            return False
        return val < other_val
