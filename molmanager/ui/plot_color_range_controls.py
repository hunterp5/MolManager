"""Shared min/max color range controls for Plotly scatter tools."""

from __future__ import annotations

from PyQt5.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from ..plot_color import parse_color_range_bounds


class PlotColorRangeControls(QWidget):
    """Min/max edits for numeric Color by columns (inline on the color options row)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._range_label = QLabel("Color range:")
        row.addWidget(self._range_label)

        self._min_label = QLabel("Min:")
        row.addWidget(self._min_label)
        self.color_min = QLineEdit()
        self.color_min.setPlaceholderText("auto")
        self.color_min.setFixedWidth(72)
        self.color_min.setToolTip("Minimum value mapped to the low end of the spectrum (empty = data min).")
        row.addWidget(self.color_min)

        self._max_label = QLabel("Max:")
        row.addWidget(self._max_label)
        self.color_max = QLineEdit()
        self.color_max.setPlaceholderText("auto")
        self.color_max.setFixedWidth(72)
        self.color_max.setToolTip("Maximum value mapped to the high end of the spectrum (empty = data max).")
        row.addWidget(self.color_max)

        self.set_enabled(False)

    def connect_changed(self, callback) -> None:
        """Call ``callback`` when the user edits min or max."""
        self.color_min.editingFinished.connect(callback)
        self.color_max.editingFinished.connect(callback)

    def parse_bounds(self) -> tuple[float | None, float | None]:
        return parse_color_range_bounds(self.color_min.text(), self.color_max.text())

    def set_enabled(self, enabled: bool) -> None:
        for widget in (
            self._range_label,
            self._min_label,
            self.color_min,
            self._max_label,
            self.color_max,
        ):
            widget.setEnabled(enabled)
