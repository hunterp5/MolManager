"""Matched molecular pair (MMP) analysis dialog (Tools → MMP)."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QVBoxLayout,
)

from ..data_analysis import numeric_subset, table_to_dataframe
from ..qt_widget_utils import make_window_minimizable
from .scope import selection_scope_checked


@dataclass(frozen=True)
class MmpDialogParams:
    """Arguments from :class:`MmpDialog` for the worker."""

    structure_source: str
    activity_column: str
    max_cuts: int
    max_variable_heavy_atoms: int
    min_activity_difference: float
    write_to_table: bool


class MmpDialog(QDialog):
    """Configure matched molecular pair analysis on table structures and an activity column."""

    def __init__(
        self,
        *,
        structure_sources: list[str],
        activity_columns: list[str],
        selected_row_count: int,
        parent=None,
    ):
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("Matched Molecular Pairs (MMP)")
        self.setMinimumWidth(440)
        self.resize(500, 0)
        self._have_selection = selected_row_count > 0

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        form = QFormLayout()
        self.src_combo = QComboBox()
        self.src_combo.addItems(structure_sources)
        form.addRow("Molecules from:", self.src_combo)

        self.activity_combo = QComboBox()
        self.activity_combo.setMinimumWidth(220)
        if activity_columns:
            self.activity_combo.addItems(activity_columns)
        else:
            self.activity_combo.addItem("(no numeric columns)")
            self.activity_combo.setEnabled(False)
        self.activity_combo.setToolTip(
            "Biological activity (or other numeric property) used to compute Δactivity for each pair."
        )
        form.addRow("Activity column:", self.activity_combo)

        self.max_cuts_sb = QSpinBox()
        self.max_cuts_sb.setRange(1, 3)
        self.max_cuts_sb.setValue(1)
        self.max_cuts_sb.setToolTip(
            "Maximum number of acyclic cuts (1 = classic single-point MMP transforms)."
        )
        form.addRow("Max cuts:", self.max_cuts_sb)

        self.max_var_atoms_sb = QSpinBox()
        self.max_var_atoms_sb.setRange(1, 50)
        self.max_var_atoms_sb.setValue(13)
        self.max_var_atoms_sb.setToolTip(
            "Ignore pairs whose changing fragment exceeds this many heavy atoms."
        )
        form.addRow("Max variable heavy atoms:", self.max_var_atoms_sb)

        self.min_dact_sb = QDoubleSpinBox()
        self.min_dact_sb.setDecimals(6)
        self.min_dact_sb.setRange(0.0, 1e12)
        self.min_dact_sb.setValue(0.0)
        self.min_dact_sb.setSingleStep(0.1)
        self.min_dact_sb.setToolTip(
            "Report only pairs whose absolute activity difference is at least this value. "
            "0 reports all pairs."
        )
        form.addRow("Minimum activity difference:", self.min_dact_sb)
        root.addLayout(form)

        self.only_selected_cb = QCheckBox("Selected Rows Only")
        self._only_selected_scope_prefix = "Selected Rows Only"
        if self._have_selection:
            self.only_selected_cb.setText(
                f"{self._only_selected_scope_prefix} ({selected_row_count} row(s))"
            )
        else:
            self.only_selected_cb.setEnabled(False)
        root.addWidget(self.only_selected_cb)

        self.write_table_cb = QCheckBox("Write MMP annotations to the main table")
        self.write_table_cb.setChecked(True)
        self.write_table_cb.setToolTip(
            "Add MMP_Partners, MMP_Transforms, and MMP_Delta_<activity> columns "
            "for molecules that participate in at least one pair."
        )
        root.addWidget(self.write_table_cb)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        ok = box.button(QDialogButtonBox.Ok)
        if ok is not None:
            ok.setEnabled(bool(activity_columns))
        root.addWidget(box)
        make_window_minimizable(self)

    def only_selected_rows(self) -> bool:
        return selection_scope_checked(self)

    def params(self) -> MmpDialogParams:
        return MmpDialogParams(
            structure_source=self.src_combo.currentText(),
            activity_column=self.activity_combo.currentText(),
            max_cuts=int(self.max_cuts_sb.value()),
            max_variable_heavy_atoms=int(self.max_var_atoms_sb.value()),
            min_activity_difference=float(self.min_dact_sb.value()),
            write_to_table=bool(self.write_table_cb.isChecked()),
        )


def activity_columns_for_mmp(parent_app, *, only_selected: bool = False) -> list[str]:
    """Numeric table columns suitable as MMP activity (Y) inputs."""
    if parent_app is None:
        return []
    try:
        df, _ = table_to_dataframe(
            parent_app,
            visible_only=False,
            only_selected=only_selected,
        )
        num = numeric_subset(df, exclude_id=True)
        return [str(c) for c in num.columns]
    except Exception:
        return []
