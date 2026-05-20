"""Delegates that paint logical (OID) row selection without Qt selection-model cost."""

from __future__ import annotations

from PyQt5.QtCore import QModelIndex, QSortFilterProxyModel
from PyQt5.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QStyledItemDelegate

from .compound_table_model import CompoundTableModel


def source_row_for_view_index(index: QModelIndex, compound_model: CompoundTableModel) -> int:
    """Map a view (possibly proxy) index to a source-model row index."""
    if not index.isValid():
        return -1
    model = index.model()
    if isinstance(model, QSortFilterProxyModel):
        src = model.mapToSource(index)
        if not src.isValid():
            return -1
        return int(src.row())
    return int(index.row())


def row_is_highlighted(index: QModelIndex, compound_model: CompoundTableModel | None) -> bool:
    if compound_model is None:
        return False
    row = source_row_for_view_index(index, compound_model)
    if row < 0:
        return False
    return compound_model.is_row_highlighted(row)


class RowHighlightDelegate(QStyledItemDelegate):
    """Default table delegate: paints Qt selection chrome for logical OID highlights."""

    def __init__(self, compound_model: CompoundTableModel, parent=None) -> None:
        super().__init__(parent)
        self._compound_model = compound_model

    def paint(self, painter, option, index) -> None:  # noqa: N802
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        if row_is_highlighted(index, self._compound_model):
            opt.state |= QStyle.State_Selected
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
