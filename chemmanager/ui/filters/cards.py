"""Filter panel cards (numeric range, substructure, text, category)."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSizePolicy,
    QVBoxLayout,
)

from rdkit import Chem

from ...utils import safe_float

# --- Filter cards (fixed height; panel scrolls) --------------------------------
_FILTER_CARD_HEIGHT_RANGE = 218
_FILTER_CARD_HEIGHT_SUBSTRUCTURE = 104
_FILTER_CARD_HEIGHT_TEXT = 156
_FILTER_CARD_HEIGHT_CATEGORY = 248
_FC_PAD = 6
_FC_GAP = 4
_FC_CTRL_H = 22
_FC_SLIDER_H = 16
_FC_LIST_MAX = 138

_INVERT_BTN_IDLE = (
    "QPushButton { padding: 2px 8px; font-size: 11px; border: 1px solid #c5cad3; border-radius: 3px; "
    "background: #ffffff; color: #33353a; }"
    "QPushButton:hover { background: #eef0f4; border-color: #aeb5c0; }"
)
_INVERT_BTN_ACTIVE = (
    "QPushButton { padding: 2px 8px; font-size: 11px; border: 1px solid #3d6fb0; border-radius: 3px; "
    "background: #4a7ec2; color: #ffffff; font-weight: 600; }"
    "QPushButton:hover { background: #3f6eb0; border-color: #355f9a; }"
)


def _fc_card_stylesheet() -> str:
    h = _FC_CTRL_H
    return f"""
    QFrame#FilterCard {{
        background-color: #f4f5f7;
        border: 1px solid #d5d8de;
        border-radius: 5px;
    }}
    QFrame#FilterCard QLabel {{
        font-size: 11px;
        color: #33353a;
        background: transparent;
    }}
    QFrame#FilterCard QComboBox, QFrame#FilterCard QLineEdit {{
        min-height: {h}px;
        max-height: {h}px;
        font-size: 11px;
        border: 1px solid #c9ced6;
        border-radius: 3px;
        padding: 1px 6px;
        background: #ffffff;
    }}
    QFrame#FilterCard QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 18px;
        border-left: 1px solid #c9ced6;
    }}
    QFrame#FilterCard QPushButton#fcRemove {{
        min-width: 22px; max-width: 22px;
        min-height: 22px; max-height: 22px;
        color: #9e2a23;
        background: #fdecea;
        border: 1px solid #e59894;
        border-radius: 4px;
        font-size: 14px;
        font-weight: bold;
        padding: 0px;
    }}
    QFrame#FilterCard QPushButton#fcRemove:hover {{
        background: #fad7d4;
        border-color: #d67a76;
    }}
    QFrame#FilterCard QSlider::groove:horizontal {{
        height: 4px;
        background: #dde0e6;
        border-radius: 2px;
    }}
    QFrame#FilterCard QSlider::handle:horizontal {{
        width: 11px;
        height: 11px;
        margin: -5px 0;
        background: #5b88c7;
        border: 1px solid #4271b6;
        border-radius: 5px;
    }}
    QFrame#FilterCard QListWidget {{
        font-size: 11px;
        border: 1px solid #c9ced6;
        border-radius: 3px;
        background: #ffffff;
        outline: 0;
    }}
    """


def _fc_install_card_shell(card: QFrame, height_px: int) -> None:
    card.setObjectName("FilterCard")
    card.setFrameShape(QFrame.StyledPanel)
    card.setFixedHeight(height_px)
    card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    card.setStyleSheet(_fc_card_stylesheet())


def style_filter_card_remove_button(btn: QPushButton) -> None:
    btn.setObjectName("fcRemove")
    btn.setText("×")
    btn.setFixedSize(22, 22)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip("Remove this filter")
    btn.setAutoDefault(False)
    btn.setDefault(False)


def _fc_toggle_btn(btn: QPushButton) -> None:
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setFixedHeight(_FC_CTRL_H)


def _fc_center_button_pair(layout: QHBoxLayout, left: QPushButton, right: QPushButton) -> None:
    layout.addStretch(1)
    layout.addWidget(left)
    layout.addWidget(right)
    layout.addStretch(1)


class _FilterCardEnableInvertMixin:
    """Shared On/Off + Invert row for filter cards. Subclasses must define ``changed = pyqtSignal()``."""

    def _fc_install_enable_invert_row(self, parent_layout: QVBoxLayout, invert_tooltip: str) -> None:
        ctl = QHBoxLayout()
        ctl.setSpacing(_FC_GAP)
        self._filter_enabled_on = True
        self.enable_btn = QPushButton("On")
        self.enable_btn.setToolTip("Turn this filter on or off.")
        self.enable_btn.clicked.connect(self._on_enable_clicked)
        _fc_toggle_btn(self.enable_btn)
        self._sync_enable_button_appearance()
        self._invert_on = False
        self.invert_btn = QPushButton("Invert")
        self.invert_btn.setToolTip(invert_tooltip)
        self.invert_btn.clicked.connect(self._on_invert_clicked)
        _fc_toggle_btn(self.invert_btn)
        self._sync_invert_button_appearance()
        _fc_center_button_pair(ctl, self.enable_btn, self.invert_btn)
        parent_layout.addLayout(ctl)

    def _sync_invert_button_appearance(self) -> None:
        if self._invert_on:
            self.invert_btn.setText("Inverted")
            self.invert_btn.setStyleSheet(_INVERT_BTN_ACTIVE)
        else:
            self.invert_btn.setText("Invert")
            self.invert_btn.setStyleSheet(_INVERT_BTN_IDLE)

    def _on_invert_clicked(self) -> None:
        self._invert_on = not self._invert_on
        self._sync_invert_button_appearance()
        self.changed.emit()

    def _sync_enable_button_appearance(self) -> None:
        if self._filter_enabled_on:
            self.enable_btn.setText("On")
            self.enable_btn.setStyleSheet(_INVERT_BTN_ACTIVE)
        else:
            self.enable_btn.setText("Off")
            self.enable_btn.setStyleSheet(_INVERT_BTN_IDLE)

    def _on_enable_clicked(self) -> None:
        self._filter_enabled_on = not self._filter_enabled_on
        self._sync_enable_button_appearance()
        self.changed.emit()

    def filter_enabled(self) -> bool:
        return self._filter_enabled_on

    def filter_inverted(self) -> bool:
        return self._invert_on

    def restore_filter_flags(self, enabled: bool = True, inverted: bool = False) -> None:
        self._filter_enabled_on = bool(enabled)
        self._sync_enable_button_appearance()
        self._invert_on = bool(inverted)
        self._sync_invert_button_appearance()


class FilterCard(_FilterCardEnableInvertMixin, QFrame):
    changed = pyqtSignal()
    removed = pyqtSignal(object)

    def __init__(self, props, app, initial_property: str | None = None):
        super().__init__()
        self.app, self.scale = app, 100
        self._active_slider = None  # "min" | "max" | None
        _fc_install_card_shell(self, _FILTER_CARD_HEIGHT_RANGE)
        l = QVBoxLayout(self)
        l.setContentsMargins(_FC_PAD, _FC_PAD, _FC_PAD, _FC_PAD)
        l.setSpacing(_FC_GAP)
        h = QHBoxLayout()
        h.setSpacing(_FC_GAP)
        self.cb = QComboBox()
        self.cb.addItems(props)
        if initial_property and self.cb.findText(initial_property) >= 0:
            self.cb.setCurrentText(initial_property)
        self.cb.currentTextChanged.connect(self.refresh_limits)
        rem = QPushButton()
        style_filter_card_remove_button(rem)
        rem.clicked.connect(lambda: self.removed.emit(self))
        h.addWidget(self.cb, 1, Qt.AlignVCenter)
        h.addWidget(rem, 0, Qt.AlignVCenter)
        l.addLayout(h)
        min_lyt = QHBoxLayout()
        min_lyt.setSpacing(_FC_GAP)
        min_lyt.addWidget(QLabel("Min"))
        self.min_edit = QLineEdit()
        self.min_edit.setFixedWidth(72)
        self.min_edit.editingFinished.connect(self.sync_from_text)
        min_lyt.addWidget(self.min_edit, 1)
        l.addLayout(min_lyt)
        self.s_min = QSlider(Qt.Horizontal)
        self.s_min.setFixedHeight(_FC_SLIDER_H)
        self.s_min.sliderPressed.connect(lambda: setattr(self, "_active_slider", "min"))
        self.s_min.sliderReleased.connect(lambda: setattr(self, "_active_slider", None))
        self.s_min.valueChanged.connect(lambda: self.sync_from_slider("min"))
        l.addWidget(self.s_min)
        max_lyt = QHBoxLayout()
        max_lyt.setSpacing(_FC_GAP)
        max_lyt.addWidget(QLabel("Max"))
        self.max_edit = QLineEdit()
        self.max_edit.setFixedWidth(72)
        self.max_edit.editingFinished.connect(self.sync_from_text)
        max_lyt.addWidget(self.max_edit, 1)
        l.addLayout(max_lyt)
        self.s_max = QSlider(Qt.Horizontal)
        self.s_max.setFixedHeight(_FC_SLIDER_H)
        self.s_max.sliderPressed.connect(lambda: setattr(self, "_active_slider", "max"))
        self.s_max.sliderReleased.connect(lambda: setattr(self, "_active_slider", None))
        self.s_max.valueChanged.connect(lambda: self.sync_from_slider("max"))
        l.addWidget(self.s_max)
        self._fc_install_enable_invert_row(l, "Show rows outside the min/max range.")
        self.refresh_limits()

    def update_prop_list(self, new_props, old_n=None, new_n=None):
        self.cb.blockSignals(True)
        current = self.cb.currentText()
        self.cb.clear()
        self.cb.addItems(new_props)
        if old_n and current == old_n:
            if new_n:
                self.cb.setCurrentText(new_n)
                self.cb.blockSignals(False)
                self.refresh_limits()
                return False
            self.cb.blockSignals(False)
            return True
        self.cb.setCurrentText(current)
        self.cb.blockSignals(False)
        return False

    def refresh_limits(self):
        prop = self.cb.currentText()
        if not prop:
            return
        self.blockSignals(True)
        b_meta = self.app.global_bounds.get(prop, {"min": 0, "max": 100, "is_int": False})
        b_min, b_max = b_meta["min"], b_meta["max"]
        self.scale = 1 if b_meta["is_int"] else 100
        self.s_min.setRange(int(b_min * self.scale), int(b_max * self.scale))
        self.s_max.setRange(int(b_min * self.scale), int(b_max * self.scale))
        self.s_min.setValue(int(b_min * self.scale))
        self.s_max.setValue(int(b_max * self.scale))
        fmt = "{:.0f}" if b_meta["is_int"] else "{:.2f}"
        self.min_edit.setText(fmt.format(b_min))
        self.max_edit.setText(fmt.format(b_max))
        self.blockSignals(False)
        self.changed.emit()

    def sync_from_slider(self, which: str | None = None):
        active = which or getattr(self, "_active_slider", None)
        vmin = self.s_min.value()
        vmax = self.s_max.value()
        if vmin > vmax:
            self.blockSignals(True)
            if active == "max":
                self.s_min.setValue(vmax)
                vmin = vmax
            else:
                self.s_max.setValue(vmin)
                vmax = vmin
            self.blockSignals(False)
        fmt = "{:.0f}" if self.scale == 1 else "{:.2f}"
        self.min_edit.setText(fmt.format(vmin / self.scale))
        self.max_edit.setText(fmt.format(vmax / self.scale))
        self.changed.emit()

    def sync_from_text(self):
        try:
            v_min, v_max = float(self.min_edit.text()), float(self.max_edit.text())
            if v_min > v_max:
                v_min = v_max
            self.blockSignals(True)
            self.s_min.setValue(int(v_min * self.scale))
            self.s_max.setValue(int(v_max * self.scale))
            self.blockSignals(False)
            self.changed.emit()
        except Exception:
            self.sync_from_slider(None)

    def get_cfg(self):
        return {
            "p": self.cb.currentText(),
            "min": self.s_min.value() / self.scale,
            "max": self.s_max.value() / self.scale,
            "enabled": self._filter_enabled_on,
            "inverted": self._invert_on,
        }

    def restore_state(self, prop: str, min_val: float, max_val: float) -> None:
        self.blockSignals(True)
        if self.cb.findText(prop) >= 0:
            self.cb.setCurrentText(prop)
        b_meta = self.app.global_bounds.get(prop, {"min": 0, "max": 100, "is_int": False})
        self.scale = 1 if b_meta["is_int"] else 100
        lo, hi = float(b_meta["min"]), float(b_meta["max"])
        self.s_min.setRange(int(lo * self.scale), int(hi * self.scale))
        self.s_max.setRange(int(lo * self.scale), int(hi * self.scale))
        lo_v = max(min(min_val, max_val), lo)
        hi_v = min(max(max_val, min_val), hi)
        self.s_min.setValue(int(lo_v * self.scale))
        self.s_max.setValue(int(hi_v * self.scale))
        fmt = "{:.0f}" if self.scale == 1 else "{:.2f}"
        self.min_edit.setText(fmt.format(self.s_min.value() / self.scale))
        self.max_edit.setText(fmt.format(self.s_max.value() / self.scale))
        self.blockSignals(False)


class SubstructureFilterCard(_FilterCardEnableInvertMixin, QFrame):
    changed = pyqtSignal()
    removed = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._last_smarts = ""
        self._last_query = None
        _fc_install_card_shell(self, _FILTER_CARD_HEIGHT_SUBSTRUCTURE)
        l = QVBoxLayout(self)
        l.setContentsMargins(_FC_PAD, _FC_PAD, _FC_PAD, _FC_PAD)
        l.setSpacing(_FC_GAP)
        row = QHBoxLayout()
        row.setSpacing(_FC_GAP)
        self.smarts_edit = QLineEdit()
        self.smarts_edit.setPlaceholderText("SMARTS pattern, e.g. c1ccccc1")
        self.smarts_edit.textChanged.connect(self._on_change)
        row.addWidget(self.smarts_edit, 1, Qt.AlignVCenter)
        rem = QPushButton()
        style_filter_card_remove_button(rem)
        rem.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(rem, 0, Qt.AlignVCenter)
        l.addLayout(row)
        self._fc_install_enable_invert_row(l, "Hide rows that match SMARTS instead of showing them.")

    def _on_change(self, _txt: str) -> None:
        self._last_query = None
        self._last_smarts = ""
        self.changed.emit()

    def _compiled_query(self):
        s = (self.smarts_edit.text() or "").strip()
        if not s:
            self._last_smarts = ""
            self._last_query = None
            return None
        if s == self._last_smarts:
            return self._last_query
        q = Chem.MolFromSmarts(s)
        self._last_smarts = s
        self._last_query = q
        return q

    def match_mol(self, mol) -> bool:
        q = self._compiled_query()
        if q is None:
            s = (self.smarts_edit.text() or "").strip()
            return True if not s else False
        try:
            return bool(mol is not None and mol.HasSubstructMatch(q))
        except Exception:
            return False

    def get_cfg(self):
        return {
            "type": "substructure",
            "smarts": (self.smarts_edit.text() or "").strip(),
            "enabled": self._filter_enabled_on,
            "inverted": self._invert_on,
        }

    def set_smarts(self, smarts: str) -> None:
        self.smarts_edit.blockSignals(True)
        self.smarts_edit.setText(smarts or "")
        self.smarts_edit.blockSignals(False)
        self._last_smarts = ""
        self._last_query = None


class TextFilterCard(_FilterCardEnableInvertMixin, QFrame):
    """Filter rows by text in a chosen column (partial or exact, case optional)."""

    changed = pyqtSignal()
    removed = pyqtSignal(object)

    def __init__(self, columns: list[str], app):
        super().__init__()
        self.app = app
        self._case_sensitive = False
        self._partial_match = True
        _fc_install_card_shell(self, _FILTER_CARD_HEIGHT_TEXT)
        l = QVBoxLayout(self)
        l.setContentsMargins(_FC_PAD, _FC_PAD, _FC_PAD, _FC_PAD)
        l.setSpacing(_FC_GAP)
        row = QHBoxLayout()
        row.setSpacing(_FC_GAP)
        self.cb = QComboBox()
        self.cb.addItems(columns)
        self.cb.currentTextChanged.connect(lambda _t: self.changed.emit())
        row.addWidget(self.cb, 1, Qt.AlignVCenter)
        rem = QPushButton()
        style_filter_card_remove_button(rem)
        rem.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(rem, 0, Qt.AlignVCenter)
        l.addLayout(row)
        self.text_edit = QLineEdit()
        self._sync_match_placeholder()
        self.text_edit.textChanged.connect(lambda _t: self.changed.emit())
        l.addWidget(self.text_edit)
        opt = QHBoxLayout()
        opt.setSpacing(_FC_GAP)
        self.case_btn = QPushButton("Case")
        self.case_btn.setToolTip("Case-sensitive vs ignore case.")
        self.case_btn.clicked.connect(self._on_case_clicked)
        _fc_toggle_btn(self.case_btn)
        self._sync_case_button_appearance()
        self.partial_btn = QPushButton("Partial")
        self.partial_btn.setToolTip("Substring vs exact cell match.")
        self.partial_btn.clicked.connect(self._on_partial_clicked)
        _fc_toggle_btn(self.partial_btn)
        self._sync_partial_button_appearance()
        _fc_center_button_pair(opt, self.case_btn, self.partial_btn)
        l.addLayout(opt)
        self._fc_install_enable_invert_row(l, "Invert matching rows.")

    def _sync_match_placeholder(self) -> None:
        if self._partial_match:
            self.text_edit.setPlaceholderText("Match substring (empty = no filter)")
        else:
            self.text_edit.setPlaceholderText("Match full cell (empty = no filter)")

    def _sync_case_button_appearance(self) -> None:
        if self._case_sensitive:
            self.case_btn.setText("Case")
            self.case_btn.setStyleSheet(_INVERT_BTN_ACTIVE)
        else:
            self.case_btn.setText("Ignore case")
            self.case_btn.setStyleSheet(_INVERT_BTN_IDLE)

    def _on_case_clicked(self) -> None:
        self._case_sensitive = not self._case_sensitive
        self._sync_case_button_appearance()
        self.changed.emit()

    def _sync_partial_button_appearance(self) -> None:
        if self._partial_match:
            self.partial_btn.setText("Partial")
            self.partial_btn.setStyleSheet(_INVERT_BTN_ACTIVE)
        else:
            self.partial_btn.setText("Exact")
            self.partial_btn.setStyleSheet(_INVERT_BTN_IDLE)

    def _on_partial_clicked(self) -> None:
        self._partial_match = not self._partial_match
        self._sync_partial_button_appearance()
        self._sync_match_placeholder()
        self.changed.emit()

    def set_column(self, name: str) -> None:
        if name and self.cb.findText(name) >= 0:
            self.cb.setCurrentText(name)

    def update_prop_list(self, new_props, old_n=None, new_n=None):
        self.cb.blockSignals(True)
        current = self.cb.currentText()
        self.cb.clear()
        self.cb.addItems(new_props)
        if old_n and current == old_n:
            if new_n:
                self.cb.setCurrentText(new_n)
                self.cb.blockSignals(False)
                self.changed.emit()
                return False
            self.cb.blockSignals(False)
            return True
        if current and self.cb.findText(current) >= 0:
            self.cb.setCurrentText(current)
        elif self.cb.count():
            self.cb.setCurrentIndex(0)
        self.cb.blockSignals(False)
        return False

    def row_matches(self, row: int) -> bool:
        prop = self.cb.currentText()
        if not prop:
            return True
        raw = self.app._table_model.value_for_header(row, prop) or ""
        needle = (self.text_edit.text() or "").strip()
        if not needle:
            return True
        if self._partial_match:
            if self._case_sensitive:
                inside = needle in raw
            else:
                inside = needle.lower() in raw.lower()
        else:
            if self._case_sensitive:
                inside = raw == needle
            else:
                inside = raw.lower() == needle.lower()
        return not inside if self._invert_on else inside

    def get_cfg(self):
        return {
            "p": self.cb.currentText(),
            "text": self.text_edit.text() or "",
            "enabled": self._filter_enabled_on,
            "inverted": self._invert_on,
            "case_sensitive": self._case_sensitive,
            "partial_match": self._partial_match,
        }

    def restore_from_session(
        self,
        prop: str,
        text: str,
        *,
        case_sensitive: bool = False,
        partial_match: bool = True,
    ) -> None:
        self.cb.blockSignals(True)
        if prop and self.cb.findText(prop) >= 0:
            self.cb.setCurrentText(prop)
        self.cb.blockSignals(False)
        self.text_edit.blockSignals(True)
        self.text_edit.setText(text or "")
        self.text_edit.blockSignals(False)
        self._case_sensitive = bool(case_sensitive)
        self._partial_match = bool(partial_match)
        self._sync_case_button_appearance()
        self._sync_partial_button_appearance()
        self._sync_match_placeholder()


class CategoryFilterCard(_FilterCardEnableInvertMixin, QFrame):
    """Filter rows by membership in selected distinct values of a column."""

    changed = pyqtSignal()
    removed = pyqtSignal(object)
    _BLANK = "\u0000blank\u0000"

    def __init__(self, columns: list[str], app):
        super().__init__()
        self.app = app
        _fc_install_card_shell(self, _FILTER_CARD_HEIGHT_CATEGORY)
        l = QVBoxLayout(self)
        l.setContentsMargins(_FC_PAD, _FC_PAD, _FC_PAD, _FC_PAD)
        l.setSpacing(_FC_GAP)
        title_row = QHBoxLayout()
        title_row.setSpacing(_FC_GAP)
        title = QLabel("Category")
        title.setStyleSheet("font-weight: 600; font-size: 11px; color: #1a1c21; background: transparent;")
        title_row.addWidget(title, 0, Qt.AlignVCenter)
        title_row.addStretch(1)
        rem = QPushButton()
        style_filter_card_remove_button(rem)
        rem.clicked.connect(lambda: self.removed.emit(self))
        title_row.addWidget(rem, 0, Qt.AlignVCenter)
        l.addLayout(title_row)
        row = QHBoxLayout()
        row.setSpacing(_FC_GAP)
        self.cb = QComboBox()
        self.cb.addItems(columns)
        self.cb.currentTextChanged.connect(self._on_column_changed)
        row.addWidget(self.cb, 1, Qt.AlignVCenter)
        ref = QPushButton("Refresh")
        ref.setToolTip("Reload values for this column.")
        ref.clicked.connect(lambda: self._populate_list())
        _fc_toggle_btn(ref)
        ref.setStyleSheet(_INVERT_BTN_IDLE)
        row.addWidget(ref, 0, Qt.AlignVCenter)
        l.addLayout(row)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.NoSelection)
        self.list_widget.setMaximumHeight(_FC_LIST_MAX)
        # Cached for ``row_matches`` during a single ``apply_filters`` pass (avoid O(rows × list) work).
        self._category_checked_cache: frozenset[str] | None = None
        self._category_n_checkable_cache: int | None = None
        self.list_widget.itemChanged.connect(self._on_category_list_item_changed)
        l.addWidget(self.list_widget)
        self._fc_install_enable_invert_row(l, "Invert category selection.")
        self._populate_list()

    def _bust_category_selection_cache(self) -> None:
        self._category_checked_cache = None
        self._category_n_checkable_cache = None

    def _on_category_list_item_changed(self, _it) -> None:
        self._bust_category_selection_cache()
        self.changed.emit()

    def _ensure_category_filter_cache(self) -> None:
        if self._category_checked_cache is not None:
            return
        out: list[str] = []
        n_checkable = 0
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.flags() & Qt.ItemIsUserCheckable:
                n_checkable += 1
                if it.checkState() == Qt.Checked:
                    out.append(self._role_value(it))
        self._category_checked_cache = frozenset(out)
        self._category_n_checkable_cache = n_checkable

    def _on_column_changed(self, _t: str) -> None:
        self._populate_list()

    def set_column(self, name: str) -> None:
        if name and self.cb.findText(name) >= 0:
            self.cb.setCurrentText(name)

    def _role_value(self, item: QListWidgetItem) -> str:
        d = item.data(Qt.UserRole)
        if d == self._BLANK:
            return ""
        return str(d) if d is not None else (item.text() or "")

    def _populate_list(self, select_values: frozenset[str] | None = None) -> None:
        self._bust_category_selection_cache()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        prop = self.cb.currentText()
        if not prop or prop not in self.app.headers:
            self.list_widget.blockSignals(False)
            self.changed.emit()
            return
        seen: set[str] = set()
        ordered: list[str] = []
        for r in range(self.app._table_model.rowCount()):
            v = self.app._table_model.value_for_header(r, prop) or ""
            if v not in seen:
                seen.add(v)
                ordered.append(v)
        ordered.sort(key=lambda x: x.lower())
        cap = 2000
        truncated = len(ordered) > cap
        for v in ordered[:cap]:
            label = "(blank)" if v == "" else v
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, self._BLANK if v == "" else v)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            want = select_values
            if want is None:
                it.setCheckState(Qt.Checked)
            else:
                it.setCheckState(Qt.Checked if v in want else Qt.Unchecked)
            self.list_widget.addItem(it)
        self.list_widget.blockSignals(False)
        if truncated:
            tip = QListWidgetItem(f"… ({len(ordered)} distinct; showing first {cap})")
            tip.setFlags(tip.flags() & ~Qt.ItemIsUserCheckable)
            self.list_widget.addItem(tip)
        self.changed.emit()

    def update_prop_list(self, new_props, old_n=None, new_n=None):
        self.cb.blockSignals(True)
        current = self.cb.currentText()
        self.cb.clear()
        self.cb.addItems(new_props)
        if old_n and current == old_n:
            if new_n:
                self.cb.setCurrentText(new_n)
                self.cb.blockSignals(False)
                self._populate_list()
                return False
            self.cb.blockSignals(False)
            return True
        if current and self.cb.findText(current) >= 0:
            self.cb.setCurrentText(current)
        elif self.cb.count():
            self.cb.setCurrentIndex(0)
        self.cb.blockSignals(False)
        self._populate_list()
        return False

    def _checked_values(self) -> frozenset[str]:
        self._ensure_category_filter_cache()
        return self._category_checked_cache or frozenset()

    def row_matches(self, row: int) -> bool:
        self._ensure_category_filter_cache()
        prop = self.cb.currentText()
        if not prop:
            return True
        raw = self.app._table_model.value_for_header(row, prop) or ""
        n_checkable = int(self._category_n_checkable_cache or 0)
        if n_checkable == 0:
            return True
        sel = self._category_checked_cache or frozenset()
        inside = raw in sel if sel else False
        if self._invert_on:
            return not inside
        return inside

    def get_cfg(self):
        return {
            "p": self.cb.currentText(),
            "values": sorted(self._checked_values()),
            "enabled": self._filter_enabled_on,
            "inverted": self._invert_on,
        }

    def restore_from_session(self, prop: str, values: list[str]) -> None:
        self.cb.blockSignals(True)
        if prop and self.cb.findText(prop) >= 0:
            self.cb.setCurrentText(prop)
        self.cb.blockSignals(False)
        self._populate_list(frozenset(str(x) for x in (values or [])))
