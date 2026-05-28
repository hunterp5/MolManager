from __future__ import annotations

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ...rdkit_fingerprints import descriptor_onbits_column_name
from ...workers import (
    DiverseSubsetWorker,
    SIMILARITY_FP_TYPE_LABELS,
    build_diverse_subset_pool,
)
from ..qt_widget_utils import make_window_minimizable
from .scope import selection_scope_checked

_DEFAULT_COLUMN = "Diverse subset rank"


class DiverseSubsetDialog(QDialog):
    """Pick a maximally diverse compound subset using fingerprint MaxMin (Tanimoto distance)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("Diverse Subset")
        self.setMinimumWidth(440)
        n_sel = len(parent._selected_logical_rows()) if parent is not None else 0
        self._have_selection = n_sel > 0
        self._scope_oids: set[int] = set()
        self._pending_column_name = ""
        self._onbits_column: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        root.addLayout(form)

        self.src_combo = QComboBox()
        self._refresh_structure_sources()
        form.addRow("Structure source:", self.src_combo)

        self.fp_combo = QComboBox()
        self.fp_combo.addItems(SIMILARITY_FP_TYPE_LABELS)
        form.addRow("Fingerprint:", self.fp_combo)

        self.subset_size_spin = QSpinBox()
        self.subset_size_spin.setRange(1, 1_000_000)
        n_rows = parent._table_model.rowCount() if parent else 1
        self.subset_size_spin.setValue(min(50, max(1, n_rows)))
        self.subset_size_spin.setToolTip(
            "Number of compounds to keep in the diverse subset (MaxMin on Tanimoto distance)."
        )
        form.addRow("Subset size:", self.subset_size_spin)

        self.only_selected_cb = QCheckBox("Only selected rows")
        self._only_selected_scope_prefix = "Only selected rows"
        if self._have_selection:
            self.only_selected_cb.setText(f"{self._only_selected_scope_prefix} ({n_sel} row(s))")
        else:
            self.only_selected_cb.setEnabled(False)
        form.addRow("", self.only_selected_cb)

        self.select_subset_cb = QCheckBox("Select subset in table")
        self.select_subset_cb.setChecked(True)
        self.select_subset_cb.setToolTip("Highlight the picked rows in the compound table.")
        form.addRow("", self.select_subset_cb)

        self.add_column_cb = QCheckBox("Add rank column")
        self.add_column_cb.setChecked(True)
        self.add_column_cb.setToolTip(
            "Rank 1 is the first MaxMin pick; higher ranks are added for diversity."
        )
        form.addRow("", self.add_column_cb)

        self.column_name_edit = QLineEdit(_DEFAULT_COLUMN)
        self.column_name_edit.setPlaceholderText("Column name in table")
        form.addRow("Rank column:", self.column_name_edit)

        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("Pick Diverse Subset")
        self.run_btn.clicked.connect(self.run)
        btn_row.addWidget(self.run_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        make_window_minimizable(self)

    def _refresh_structure_sources(self) -> None:
        self.src_combo.clear()
        if self.parent_app is None:
            return
        self.src_combo.addItems(self.parent_app.chemistry_tool_structure_sources())

    def _matching_onbits_column(self, fp_choice: str) -> str | None:
        app = self.parent_app
        if app is None:
            return None
        col = descriptor_onbits_column_name(fp_choice)
        return col if col in app.headers else None

    def _unique_column_name(self, base: str) -> str:
        name = (base or "").strip() or _DEFAULT_COLUMN
        if name not in self.parent_app.headers:
            return name
        cnt = 1
        while f"{name} ({cnt})" in self.parent_app.headers:
            cnt += 1
        return f"{name} ({cnt})"

    def _scope_oids_in_table(self, only_selected: bool) -> set[int]:
        allowed = self.parent_app._selected_oids_set() if only_selected else None
        oids: set[int] = set()
        m = self.parent_app._table_model
        for r in range(m.rowCount()):
            oid = m.row_oid(r)
            if allowed is not None and oid not in allowed:
                continue
            oids.add(oid)
        return oids

    def _onbits_values_for_scope(self, scope_oids: set[int]) -> dict[int, str] | None:
        col = self._onbits_column
        if not col or col not in self.parent_app.headers:
            return None
        hidx = self.parent_app.headers.index(col)
        out: dict[int, str] = {}
        for oid in scope_oids:
            row = self.parent_app.get_row_by_id(oid)
            if row < 0:
                continue
            out[int(oid)] = self.parent_app._table_cell_text(row, hidx) or ""
        return out

    def run(self) -> None:
        app = self.parent_app
        src = self.src_combo.currentText()
        only_sel = selection_scope_checked(self)
        if only_sel and not app._selected_oids_set():
            app.status_label.setText(
                "Diverse subset: \u201cOnly selected rows\u201d is checked but nothing is selected."
            )
            return

        self._scope_oids = self._scope_oids_in_table(only_sel)
        rows = app.collect_scoped_table_mols(src, only_selected=only_sel)
        valid = [(oid, mol) for oid, mol in rows if mol is not None]
        if not valid:
            app.status_label.setText("Diverse subset: no valid structures in this scope.")
            return

        fp_choice = self.fp_combo.currentText()
        self._onbits_column = self._matching_onbits_column(fp_choice)
        onbits_by_oid = self._onbits_values_for_scope(self._scope_oids)
        use_onbits_col = self._onbits_column is not None
        pool, err = build_diverse_subset_pool(
            valid,
            fp_choice,
            onbits_by_oid=onbits_by_oid,
            require_onbits_column=use_onbits_col,
        )
        if err:
            app.status_label.setText(f"Diverse subset: {err}")
            return
        if not pool:
            app.status_label.setText(
                "Diverse subset: no eligible rows "
                + (
                    f"with values in “{self._onbits_column}”."
                    if use_onbits_col
                    else "in this scope."
                )
            )
            return

        k = int(self.subset_size_spin.value())
        if k < 1:
            app.status_label.setText("Diverse subset: subset size must be at least 1.")
            return
        if k > len(pool):
            app.status_label.setText(
                f"Diverse subset: subset size ({k}) exceeds eligible rows ({len(pool)})."
            )
            return

        self._pending_column_name = ""
        if self.add_column_cb.isChecked():
            self._pending_column_name = self._unique_column_name(self.column_name_edit.text())

        app._diverse_subset_run_ctx = {
            "scope_oids": set(self._scope_oids),
            "pending_column_name": self._pending_column_name,
            "select_subset": self.select_subset_cb.isChecked(),
        }
        prog = app._tool_progress_state
        sig = app._ensure_diverse_subset_signals()
        app._begin_tool_progress("Diverse subset", len(pool))
        app.process_queue.enqueue(
            f"Diverse subset ({len(pool)} rows, pick {k})",
            lambda ev, r=valid, c=fp_choice, kk=k, s=sig, st=prog, ob=onbits_by_oid, uo=use_onbits_col: DiverseSubsetWorker(
                r,
                c,
                kk,
                s,
                onbits_by_oid=ob,
                use_onbits_column=uo,
                cancel_event=ev,
                progress_state=st,
            ),
        )
        self.close()
