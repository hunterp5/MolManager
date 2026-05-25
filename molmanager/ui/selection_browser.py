"""Modeless dialog: step through selected table rows with structure preview."""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import QItemSelection, QItemSelectionModel, Qt, QTimer
from PyQt5.QtGui import QImage, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QVBoxLayout,
)

from ..display_constants import (
    BROWSER_STRUCTURE_PREVIEW_MIN_HEIGHT,
    BROWSER_STRUCTURE_PREVIEW_MIN_WIDTH,
)
from .compound_table_model import CompoundTableModel
from .qt_widget_utils import make_window_minimizable


class SelectionBrowserDialog(QDialog):
    """Forward/back through the current selection or entire table; shows a structure preview."""

    def __init__(self, parent: Any = None):
        super().__init__(parent)
        self._app = parent
        self.setWindowTitle("Browser")
        self.resize(480, 520)

        self._rows: list[int] = []
        self._idx = 0
        self._preview_pix_cache: dict[tuple[int, int, int], QPixmap] = {}  # (oid, w_px, h_px) -> pixmap

        root = QVBoxLayout(self)
        self._cb_only_selected = QCheckBox("Browse Only Selected")
        self._cb_only_selected.setToolTip(
            "When checked, Browser walks only the current selection.\n"
            "When unchecked, Browser walks the entire table."
        )

        self._meta = QLabel()
        self._meta.setAlignment(Qt.AlignCenter)
        root.addWidget(self._meta)

        self._struct_label = QLabel()
        self._struct_label.setAlignment(Qt.AlignCenter)
        self._struct_label.setMinimumSize(360, 260)
        self._struct_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._struct_label.setStyleSheet(
            "background-color: palette(base); border: 1px solid palette(mid); border-radius: 4px;"
        )
        root.addWidget(self._struct_label, 1)

        self._prop_box = QGroupBox()
        # Keep this panel visually distinct (no header/title).
        self._prop_box.setStyleSheet(
            "QGroupBox { margin-top: 10px; background-color: palette(base); "
            "border: 1px solid palette(mid); border-radius: 4px; }"
        )
        self._prop_form = QFormLayout(self._prop_box)
        self._prop_form.setLabelAlignment(Qt.AlignRight)
        self._prop_form.setFormAlignment(Qt.AlignTop)
        self._prop_form.setContentsMargins(12, 12, 12, 10)
        self._prop_form.setVerticalSpacing(8)
        self._prop_form.setHorizontalSpacing(10)

        self._prop_combo_1 = QComboBox()
        self._prop_combo_2 = QComboBox()
        self._prop_combo_3 = QComboBox()
        for cb in (self._prop_combo_1, self._prop_combo_2, self._prop_combo_3):
            cb.setSizeAdjustPolicy(QComboBox.AdjustToContents)

        self._prop_value_1 = QLabel("—")
        self._prop_value_2 = QLabel("—")
        self._prop_value_3 = QLabel("—")
        for lab in (self._prop_value_1, self._prop_value_2, self._prop_value_3):
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setWordWrap(True)

        self._prop_form.addRow(self._prop_combo_1, self._prop_value_1)
        self._prop_form.addRow(self._prop_combo_2, self._prop_value_2)
        self._prop_form.addRow(self._prop_combo_3, self._prop_value_3)
        root.addWidget(self._prop_box)

        row_btns = QHBoxLayout()
        self._btn_first = QPushButton("<<")
        self._btn_first.setToolTip("First eligible row in scope (Home)")
        self._btn_back = QPushButton("← Back")
        self._btn_back.setToolTip("Previous eligible row (←)")
        self._btn_fwd = QPushButton("Forward →")
        self._btn_fwd.setToolTip("Next eligible row (→)")
        self._btn_last = QPushButton(">>")
        self._btn_last.setToolTip("Last eligible row in scope (End)")
        self._btn_toggle_select = QPushButton("Select")
        self._btn_toggle_select.setToolTip("Select or deselect this row in the table")
        row_btns.addWidget(self._btn_first)
        row_btns.addWidget(self._btn_back)
        row_btns.addWidget(self._btn_fwd)
        row_btns.addWidget(self._btn_last)
        row_btns.addWidget(self._cb_only_selected)
        row_btns.addWidget(self._btn_toggle_select)
        row_btns.addStretch()
        root.addLayout(row_btns)

        self._btn_first.clicked.connect(self._go_first)
        self._btn_back.clicked.connect(lambda: self._step(-1))
        self._btn_fwd.clicked.connect(lambda: self._step(1))
        self._btn_last.clicked.connect(self._go_last)
        self._btn_toggle_select.clicked.connect(self._toggle_current_row_selected)
        self._cb_only_selected.toggled.connect(lambda _v: self.refresh_from_app())
        self._prop_combo_1.currentIndexChanged.connect(lambda _i: self._update_property_values())
        self._prop_combo_2.currentIndexChanged.connect(lambda _i: self._update_property_values())
        self._prop_combo_3.currentIndexChanged.connect(lambda _i: self._update_property_values())

        QShortcut(QKeySequence(Qt.Key_Home), self, activated=self._go_first)
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self._step(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self._step(1))
        QShortcut(QKeySequence(Qt.Key_End), self, activated=self._go_last)

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setSingleShot(True)
        self._auto_refresh_timer.setInterval(80)
        self._auto_refresh_timer.timeout.connect(
            lambda: self.refresh_from_app(preserve_position=True)
        )

        # Default to "only selected" when there *is* a selection; otherwise browse whole table.
        try:
            has_sel = bool(self._app._selected_logical_rows())
        except Exception:
            has_sel = False
        self._cb_only_selected.setChecked(has_sel)
        self.refresh_from_app()
        self._wire_table_updates()
        make_window_minimizable(self)

    def _wire_table_updates(self) -> None:
        """Keep Browser in sync when the table, filters, or selection change."""
        if getattr(self, "_table_updates_wired", False):
            return
        app = self._app
        model = getattr(app, "_table_model", None)
        if model is None:
            return

        def _schedule_refresh() -> None:
            self._auto_refresh_timer.start()

        model.dataChanged.connect(_schedule_refresh)
        model.rowsInserted.connect(_schedule_refresh)
        model.rowsRemoved.connect(_schedule_refresh)
        model.modelReset.connect(_schedule_refresh)
        model.layoutChanged.connect(_schedule_refresh)
        model.headerDataChanged.connect(_schedule_refresh)
        proxy = getattr(app, "_filter_proxy_model", None)
        if proxy is not None:
            proxy.layoutChanged.connect(_schedule_refresh)
            proxy.modelReset.connect(_schedule_refresh)
        sm = app.table.selectionModel()
        if sm is not None:
            sm.selectionChanged.connect(_schedule_refresh)
        self._table_updates_wired = True

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._rows and 0 <= self._idx < len(self._rows):
            self._update_preview(self._rows[self._idx])

    def refresh_from_app(self, *, preserve_position: bool = False) -> None:
        """Recompute row scope; optionally keep the current row (by OID) when the table changes."""
        app = self._app
        cur_oid: int | None = None
        if preserve_position:
            row = self._current_row()
            if row is not None:
                try:
                    cur_oid = int(app._table_model.row_oid(row))
                except Exception:
                    cur_oid = None
        self._refresh_property_columns()
        self._rows = self._rows_for_scope(app)
        self._idx = 0
        if self._rows:
            if preserve_position and cur_oid is not None:
                for j, rr in enumerate(self._rows):
                    try:
                        if int(app._table_model.row_oid(rr)) == cur_oid:
                            self._idx = j
                            break
                    except Exception:
                        continue
            self._idx = self._first_navigable_index(self._idx, +1)
        self._preview_pix_cache.clear()
        self._update_ui()

    def _rows_for_scope(self, app: Any) -> list[int]:
        visible = set(app._visible_source_row_indices())
        if self._cb_only_selected.isChecked():
            raw = list(app._selected_logical_rows())
            vis = [r for r in raw if r in visible]
            return vis if vis else raw
        # Whole-table browsing: prefer visible rows so navigation always lands on something the user can see.
        n = int(app._table_model.rowCount())
        if visible:
            ordered = [r for r in range(n) if r in visible]
            if ordered:
                return ordered
        return list(range(n))

    def _row_navigable(self, r: int) -> bool:
        if not (0 <= r < self._app._table_model.rowCount()):
            return False
        return self._app._is_source_row_visible(r)

    def _first_navigable_index(self, start: int, delta: int) -> int:
        n = len(self._rows)
        if n == 0:
            return 0
        i = start % n
        for _ in range(n):
            if self._row_navigable(self._rows[i]):
                return i
            i = (i + delta) % n
        return start % n

    def _last_navigable_index(self) -> int:
        n = len(self._rows)
        if n == 0:
            return 0
        for i in range(n - 1, -1, -1):
            if self._row_navigable(self._rows[i]):
                return i
        return self._first_navigable_index(0, +1)

    def _go_first(self) -> None:
        if not self._rows:
            return
        self._idx = self._first_navigable_index(0, +1)
        self._focus_row(self._rows[self._idx])

    def _go_last(self) -> None:
        if not self._rows:
            return
        self._idx = self._last_navigable_index()
        self._focus_row(self._rows[self._idx])

    def _step(self, delta: int) -> None:
        n = len(self._rows)
        if n == 0:
            return
        if n == 1:
            self._focus_row(self._rows[0])
            return
        nxt = (self._idx + delta) % n
        # Prefer skipping rows hidden by filters so the main table view matches.
        start = nxt
        for _ in range(n):
            r = self._rows[nxt]
            if self._row_navigable(r):
                self._idx = nxt
                self._focus_row(r)
                return
            nxt = (nxt + delta) % n
            if nxt == start:
                break
        self._idx = self._first_navigable_index(self._idx, delta)
        if self._rows:
            self._focus_row(self._rows[self._idx])

    def _focus_row(self, logical_row: int) -> None:
        app = self._app
        tbl = app.table
        m = app._table_model
        if logical_row < 0 or logical_row >= m.rowCount():
            return
        ix = m.index(logical_row, CompoundTableModel.STRUCTURE_COL)
        # Update the current cell only; do not replace the user's multi-selection
        # (plain setCurrentIndex behaves like navigating to another row).
        sm = tbl.selectionModel()
        if sm is not None:
            sm.setCurrentIndex(ix, QItemSelectionModel.Current)
        tbl.scrollTo(ix, QAbstractItemView.PositionAtCenter)
        self._sync_caption(logical_row)
        self._update_preview(logical_row)

    def _sync_caption(self, logical_row: int) -> None:
        n = len(self._rows)
        scope = "Selected set" if self._cb_only_selected.isChecked() else "Table"
        self._meta.setText(f"{scope}: {self._idx + 1} / {n}  ·  Row {logical_row + 1}")
        self._update_property_values()

    def _refresh_property_columns(self) -> None:
        """Populate the 3 column pickers from current table headers, preserving selections if possible."""
        try:
            headers = list(getattr(self._app, "headers", []) or [])
        except Exception:
            headers = []
        choices = [h for h in headers if h not in ("ID_HIDDEN", "Structure")]
        if not choices:
            choices = []

        combos = (self._prop_combo_1, self._prop_combo_2, self._prop_combo_3)
        prev = [cb.currentText() for cb in combos]
        for cb in combos:
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("—", userData=None)
            for h in choices:
                cb.addItem(h, userData=h)
            cb.blockSignals(False)
        for cb, p in zip(combos, prev, strict=False):
            if p and p != "—":
                j = cb.findText(p)
                if j >= 0:
                    cb.setCurrentIndex(j)

        # Reasonable defaults: SMILES then Name-ish columns if present.
        def _set_default(cb: QComboBox, prefer: list[str]) -> None:
            if cb.currentData() is not None:
                return
            for h in prefer:
                j = cb.findText(h)
                if j >= 0:
                    cb.setCurrentIndex(j)
                    return

        _set_default(self._prop_combo_1, ["SMILES", "Name", "CompoundName", "ID"])
        _set_default(self._prop_combo_2, ["Name", "CompoundName", "CAS", "InChIKey"])
        _set_default(self._prop_combo_3, ["MW", "MolWt", "cLogP", "LogP", "TPSA"])

    def _current_row(self) -> int | None:
        if not self._rows:
            return None
        if not (0 <= self._idx < len(self._rows)):
            return None
        return self._rows[self._idx]

    def _cell_text(self, logical_row: int, header: str) -> str:
        app = self._app
        try:
            col = int(app.headers.index(header))
        except Exception:
            return ""
        return str(app._table_model.data(app._table_model.index(logical_row, col), Qt.DisplayRole) or "")

    def _is_row_selected(self, logical_row: int) -> bool:
        try:
            sm = self._app.table.selectionModel()
            if sm is None:
                return False
            return bool(sm.isRowSelected(logical_row, sm.model().index(logical_row, 0).parent()))
        except Exception:
            # Fallback: check if any selected index is on this row.
            try:
                sm = self._app.table.selectionModel()
                return any(ix.row() == logical_row for ix in (sm.selectedIndexes() if sm is not None else []))
            except Exception:
                return False

    def _toggle_current_row_selected(self) -> None:
        r = self._current_row()
        if r is None:
            return
        app = self._app
        sm = app.table.selectionModel()
        if sm is None:
            return
        top = app._table_model.index(r, 0)
        bottom = app._table_model.index(r, max(0, app._table_model.columnCount() - 1))
        sel = QItemSelection(top, bottom)
        already = self._is_row_selected(r)
        mode = QItemSelectionModel.Deselect if already else QItemSelectionModel.Select
        sm.select(sel, mode | QItemSelectionModel.Rows)

        # If browsing only selected rows, selection changes affect scope.
        if self._cb_only_selected.isChecked():
            try:
                oid = int(app._table_model.row_oid(r))
            except Exception:
                oid = None
            self.refresh_from_app()
            if oid is not None and self._rows:
                for j, rr in enumerate(self._rows):
                    try:
                        if int(app._table_model.row_oid(rr)) == oid:
                            self._idx = j
                            break
                    except Exception:
                        continue
                self._update_ui()
        else:
            self._sync_select_button()

    def _update_property_values(self) -> None:
        r = self._current_row()
        if r is None:
            self._prop_value_1.setText("—")
            self._prop_value_2.setText("—")
            self._prop_value_3.setText("—")
            return
        mapping = [
            (self._prop_combo_1, self._prop_value_1),
            (self._prop_combo_2, self._prop_value_2),
            (self._prop_combo_3, self._prop_value_3),
        ]
        for cb, lab in mapping:
            h = cb.currentData()
            if not h:
                lab.setText("—")
                continue
            v = self._cell_text(r, str(h))
            lab.setText(v if v != "" else "—")

        self._sync_select_button()

    def _sync_select_button(self) -> None:
        r = self._current_row()
        if r is None:
            self._btn_toggle_select.setEnabled(False)
            self._btn_toggle_select.setText("Select")
            return
        self._btn_toggle_select.setEnabled(True)
        self._btn_toggle_select.setText("Deselect" if self._is_row_selected(r) else "Select")

    def _structure_pixmap(self, logical_row: int):
        m = self._app._table_model
        ix = m.index(logical_row, CompoundTableModel.STRUCTURE_COL)
        return m.data(ix, Qt.DecorationRole)

    def _preview_pixel_size(self) -> tuple[int, int, float]:
        """Device-pixel width/height for a crisp Browser preview (matches label size)."""
        dpr = max(1.0, float(self.devicePixelRatioF()))
        lw = max(self._struct_label.width(), BROWSER_STRUCTURE_PREVIEW_MIN_WIDTH)
        lh = max(self._struct_label.height(), BROWSER_STRUCTURE_PREVIEW_MIN_HEIGHT)
        return int(lw * dpr), int(lh * dpr), dpr

    def _render_preview_pixmap(self, logical_row: int, pw: int, ph: int) -> QPixmap | None:
        """High-resolution 2D depiction for Browser (do not upscale the table column pixmap)."""
        try:
            from rdkit.Chem.Draw import rdMolDraw2D
        except Exception:
            return None
        app = self._app
        try:
            oid = int(app._table_model.row_oid(logical_row))
        except Exception:
            return None
        cache_key = (oid, pw, ph)
        cached = self._preview_pix_cache.get(cache_key)
        if cached is not None and not cached.isNull():
            return cached
        mol = getattr(app, "mols", {}).get(oid)
        if mol is None:
            return None
        try:
            d = rdMolDraw2D.MolDraw2DCairo(pw, ph)
            rdMolDraw2D.PrepareAndDrawMolecule(d, mol)
            d.FinishDrawing()
            img = QImage.fromData(d.GetDrawingText())
            pm = QPixmap.fromImage(img)
            if not pm.isNull():
                self._preview_pix_cache[cache_key] = pm
                return pm
        except Exception:
            return None
        return None

    def _update_preview(self, logical_row: int) -> None:
        pw, ph, dpr = self._preview_pixel_size()
        pm = self._render_preview_pixmap(logical_row, pw, ph)
        if pm is None or pm.isNull():
            table_pm = self._structure_pixmap(logical_row)
            if isinstance(table_pm, QPixmap) and not table_pm.isNull():
                if table_pm.width() >= pw * 0.9 and table_pm.height() >= ph * 0.9:
                    pm = table_pm
        if isinstance(pm, QPixmap) and not pm.isNull():
            if pm.width() != pw or pm.height() != ph:
                pm = pm.scaled(pw, ph, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self._struct_label.setPixmap(pm)
            self._struct_label.setText("")
        else:
            self._struct_label.clear()
            self._struct_label.setPixmap(QPixmap())
            self._struct_label.setText("(no structure available)")

    def _update_ui(self) -> None:
        n = len(self._rows)
        single = n <= 1
        has_rows = n > 0
        self._btn_first.setEnabled(has_rows)
        self._btn_last.setEnabled(has_rows)
        self._btn_back.setEnabled(not single and has_rows)
        self._btn_fwd.setEnabled(not single and has_rows)
        if n == 0:
            if self._cb_only_selected.isChecked():
                self._meta.setText(
                    "No rows selected — uncheck “Browse Only Selected” or select rows in the table."
                )
            else:
                self._meta.setText("Table is empty.")
            self._struct_label.clear()
            self._struct_label.setPixmap(QPixmap())
            self._struct_label.setText("")
            return
        self._idx = max(0, min(self._idx, n - 1))
        self._idx = self._first_navigable_index(self._idx, +1)
        r = self._rows[self._idx]
        self._focus_row(r)
