"""Filter panel cards (numeric range, substructure, text, category)."""

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
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
    QWidget,
)

from rdkit import Chem


# --- Filter cards (compact; panel scrolls) -------------------------------------
_FILTER_CARD_MIN_HEIGHT_RANGE = 104
_FILTER_CARD_MIN_HEIGHT_SUBSTRUCTURE = 52
_FILTER_CARD_MIN_HEIGHT_TEXT = 84
_FILTER_CARD_MIN_HEIGHT_CATEGORY = 120
_FC_PAD = 8
_FC_GAP = 3
_FC_CTRL_H = 20
_FC_SLIDER_H = 14
_FC_LIST_MAX = 96
_FC_MINI_LABEL_W = 26
_FC_TOOL_BTN_MIN_W = 48
_FC_TOOL_BTN_WIDE_MIN_W = 76


def _fc_install_card_shell(card: QFrame, min_height_px: int) -> None:
    from ..theme import filter_card_stylesheet

    card.setObjectName("FilterCard")
    card.setFrameShape(QFrame.NoFrame)
    card.setMinimumHeight(min_height_px)
    card.setMinimumWidth(0)
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    card.setStyleSheet(filter_card_stylesheet())


def _fc_card_layout(card: QFrame) -> QVBoxLayout:
    ly = QVBoxLayout(card)
    ly.setContentsMargins(_FC_PAD, _FC_PAD, _FC_PAD, _FC_PAD)
    ly.setSpacing(_FC_GAP)
    return ly


def style_filter_card_remove_button(btn: QPushButton) -> None:
    btn.setObjectName("fcRemove")
    btn.setText("×")
    btn.setFixedSize(18, 18)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip("Remove this filter")
    btn.setAutoDefault(False)
    btn.setDefault(False)


def _fc_configure_column_combo(cb: QComboBox) -> None:
    """Keep the combo within the filter panel; long names scroll in the dropdown."""
    cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLength)
    cb.setMinimumContentsLength(8)
    cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    cb.setMinimumWidth(0)


def _fc_toggle_btn(
    btn: QPushButton,
    *,
    min_width: int | None = None,
    active: bool | None = None,
) -> None:
    from ..theme import polish_widget_property

    btn.setObjectName("fcToggle")
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setFixedHeight(_FC_CTRL_H)
    btn.setMinimumWidth(int(min_width) if min_width is not None else 0)
    btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
    if active is not None:
        polish_widget_property(btn, "fcActive", bool(active))


def _fc_toolbar_button(text: str, *, wide: bool = False) -> QPushButton:
    """Compact filter-panel button matching On/Invert toggle styling."""
    btn = QPushButton(text)
    min_w = _FC_TOOL_BTN_WIDE_MIN_W if wide else _FC_TOOL_BTN_MIN_W
    _fc_toggle_btn(btn, min_width=min_w, active=False)
    return btn


def _fc_add_column_header_row(parent_layout: QVBoxLayout, combo: QComboBox, removed_cb) -> None:
    """Top row: column combo and remove control."""
    row = QHBoxLayout()
    row.setSpacing(_FC_GAP)
    row.addWidget(combo, 1, Qt.AlignVCenter)
    rem = QPushButton()
    style_filter_card_remove_button(rem)
    rem.clicked.connect(removed_cb)
    row.addWidget(rem, 0, Qt.AlignVCenter)
    parent_layout.addLayout(row)


def _fc_add_bottom_tools_row(parent_layout: QVBoxLayout, buttons: list[QPushButton]) -> None:
    """Bottom row of uniform toolbar buttons (On, Invert, …)."""
    row = QHBoxLayout()
    row.setSpacing(_FC_GAP)
    for btn in buttons:
        row.addWidget(btn, 0, Qt.AlignVCenter)
    row.addStretch(1)
    parent_layout.addLayout(row)


def _fc_set_toggle_active(btn: QPushButton, active: bool) -> None:
    from ..theme import polish_widget_property

    polish_widget_property(btn, "fcActive", bool(active))


def _fc_mini_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("fcMiniLabel")
    lbl.setFixedWidth(_FC_MINI_LABEL_W)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return lbl


class _FilterCardEnableInvertMixin:
    """Shared On/Off + Invert controls. Subclasses must define ``changed`` and ``removed`` signals."""

    def _fc_init_enable_invert(
        self,
        invert_tooltip: str,
        *,
        toolbar_min_width: int | None = None,
    ) -> None:
        self._filter_enabled_on = True
        self.enable_btn = QPushButton("On")
        self.enable_btn.setToolTip("Turn this filter on or off.")
        self.enable_btn.clicked.connect(self._on_enable_clicked)
        _fc_toggle_btn(
            self.enable_btn,
            min_width=toolbar_min_width,
            active=True,
        )
        self._sync_enable_button_appearance()
        self._invert_on = False
        self.invert_btn = QPushButton("Invert")
        self.invert_btn.setToolTip(invert_tooltip)
        self.invert_btn.clicked.connect(self._on_invert_clicked)
        _fc_toggle_btn(
            self.invert_btn,
            min_width=toolbar_min_width,
            active=False,
        )
        self._sync_invert_button_appearance()

    def _fc_add_header_toolbar(
        self,
        parent_layout: QVBoxLayout,
        *,
        leading: QWidget | None = None,
    ) -> QPushButton:
        """Header row: leading control expands; On / Invert / remove on the right."""
        row = QHBoxLayout()
        row.setSpacing(_FC_GAP)
        if leading is not None:
            row.addWidget(leading, 1, Qt.AlignVCenter)
        row.addWidget(self.enable_btn, 0, Qt.AlignVCenter)
        row.addWidget(self.invert_btn, 0, Qt.AlignVCenter)
        rem = QPushButton()
        style_filter_card_remove_button(rem)
        rem.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(rem, 0, Qt.AlignVCenter)
        parent_layout.addLayout(row)
        return rem

    def _sync_invert_button_appearance(self) -> None:
        if self._invert_on:
            self.invert_btn.setText("Inverted")
        else:
            self.invert_btn.setText("Invert")
        _fc_set_toggle_active(self.invert_btn, self._invert_on)

    def _on_invert_clicked(self) -> None:
        self._invert_on = not self._invert_on
        self._sync_invert_button_appearance()
        self.changed.emit()

    def _sync_enable_button_appearance(self) -> None:
        if self._filter_enabled_on:
            self.enable_btn.setText("On")
        else:
            self.enable_btn.setText("Off")
        _fc_set_toggle_active(self.enable_btn, self._filter_enabled_on)

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

    def refresh_theme_styles(self) -> None:
        """Re-apply toggle state after the card stylesheet changes (theme switch)."""
        self._sync_enable_button_appearance()
        self._sync_invert_button_appearance()
        sync_case = getattr(self, "_sync_case_button_appearance", None)
        if callable(sync_case):
            sync_case()
        sync_partial = getattr(self, "_sync_partial_button_appearance", None)
        if callable(sync_partial):
            sync_partial()


class FilterCard(_FilterCardEnableInvertMixin, QFrame):
    changed = pyqtSignal()
    removed = pyqtSignal(object)

    def __init__(self, props, app, initial_property: str | None = None):
        super().__init__()
        self.app, self.scale = app, 100
        self._active_slider = None  # "min" | "max" | None
        _fc_install_card_shell(self, _FILTER_CARD_MIN_HEIGHT_RANGE)
        l = _fc_card_layout(self)
        self.cb = QComboBox()
        _fc_configure_column_combo(self.cb)
        self.cb.addItems(props)
        if initial_property and self.cb.findText(initial_property) >= 0:
            self.cb.setCurrentText(initial_property)
        self.cb.currentTextChanged.connect(self.refresh_limits)
        self._fc_init_enable_invert("Show rows outside the min/max range.")
        self._fc_add_header_toolbar(l, leading=self.cb)

        min_lyt = QHBoxLayout()
        min_lyt.setSpacing(_FC_GAP)
        min_lyt.addWidget(_fc_mini_label("Min"))
        self.min_edit = QLineEdit()
        self.min_edit.setMinimumWidth(40)
        self.min_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.min_edit.editingFinished.connect(self.sync_from_text)
        min_lyt.addWidget(self.min_edit, 1)
        l.addLayout(min_lyt)
        self.s_min = QSlider(Qt.Horizontal)
        self.s_min.setFixedHeight(_FC_SLIDER_H)
        self.s_min.sliderPressed.connect(lambda: setattr(self, "_active_slider", "min"))
        self.s_min.sliderReleased.connect(lambda: setattr(self, "_active_slider", None))
        self.s_min.valueChanged.connect(lambda: self._sync_slider_edits("min"))
        self.s_min.sliderReleased.connect(self._commit_slider_filter)
        l.addWidget(self.s_min)

        max_lyt = QHBoxLayout()
        max_lyt.setSpacing(_FC_GAP)
        max_lyt.addWidget(_fc_mini_label("Max"))
        self.max_edit = QLineEdit()
        self.max_edit.setMinimumWidth(40)
        self.max_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.max_edit.editingFinished.connect(self.sync_from_text)
        max_lyt.addWidget(self.max_edit, 1)
        l.addLayout(max_lyt)
        self.s_max = QSlider(Qt.Horizontal)
        self.s_max.setFixedHeight(_FC_SLIDER_H)
        self.s_max.sliderPressed.connect(lambda: setattr(self, "_active_slider", "max"))
        self.s_max.sliderReleased.connect(lambda: setattr(self, "_active_slider", None))
        self.s_max.valueChanged.connect(lambda: self._sync_slider_edits("max"))
        self.s_max.sliderReleased.connect(self._commit_slider_filter)
        l.addWidget(self.s_max)
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

    def _sync_slider_edits(self, which: str | None = None) -> None:
        """Update min/max text while dragging without re-filtering the table."""
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

    def _commit_slider_filter(self) -> None:
        self._sync_slider_edits(None)
        self.changed.emit()

    def sync_from_slider(self, which: str | None = None):
        self._sync_slider_edits(which)
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
        _fc_install_card_shell(self, _FILTER_CARD_MIN_HEIGHT_SUBSTRUCTURE)
        l = _fc_card_layout(self)
        self.smarts_edit = QLineEdit()
        self.smarts_edit.setPlaceholderText("SMARTS, e.g. [F,Cl], [!C;R], or [M]")
        self.smarts_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.smarts_edit.setMinimumWidth(0)
        self.smarts_edit.textChanged.connect(self._on_change)
        self._fc_init_enable_invert("Hide rows that match SMARTS instead of showing them.")
        self._fc_add_header_toolbar(l, leading=self.smarts_edit)

    def _on_change(self, _txt: str) -> None:
        self._last_query = None
        self._last_smarts = ""
        self.changed.emit()

    def _compiled_query(self):
        from ...smarts_patterns import mol_from_smarts

        s = (self.smarts_edit.text() or "").strip()
        if not s:
            self._last_smarts = ""
            self._last_query = None
            return None
        if s == self._last_smarts:
            return self._last_query
        q = mol_from_smarts(s)
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
        _fc_install_card_shell(self, _FILTER_CARD_MIN_HEIGHT_TEXT)
        l = _fc_card_layout(self)
        self.cb = QComboBox()
        _fc_configure_column_combo(self.cb)
        self.cb.addItems(columns)
        self.cb.currentTextChanged.connect(lambda _t: self.changed.emit())
        self._fc_init_enable_invert(
            "Invert matching rows.",
            toolbar_min_width=_FC_TOOL_BTN_MIN_W,
        )
        self.partial_btn = _fc_toolbar_button("Partial")
        self.partial_btn.setToolTip("Substring vs exact cell match.")
        self.partial_btn.clicked.connect(self._on_partial_clicked)
        self._sync_partial_button_appearance()
        self.case_btn = _fc_toolbar_button("Ignore Case", wide=True)
        self.case_btn.setToolTip("Case-sensitive vs ignore case.")
        self.case_btn.clicked.connect(self._on_case_clicked)
        self._sync_case_button_appearance()
        _fc_add_column_header_row(l, self.cb, lambda: self.removed.emit(self))
        self.text_edit = QLineEdit()
        self.text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.text_edit.setMinimumWidth(0)
        self._sync_match_placeholder()
        self._text_filter_timer = QTimer(self)
        self._text_filter_timer.setSingleShot(True)
        self._text_filter_timer.timeout.connect(self.changed.emit)
        self.text_edit.textChanged.connect(self._schedule_text_filter_changed)
        l.addWidget(self.text_edit)
        _fc_add_bottom_tools_row(
            l,
            [self.enable_btn, self.invert_btn, self.partial_btn, self.case_btn],
        )

    def _schedule_text_filter_changed(self, _text: str = "") -> None:
        self._text_filter_timer.start(280)

    def _sync_match_placeholder(self) -> None:
        if self._partial_match:
            self.text_edit.setPlaceholderText("Match substring (empty = no filter)")
        else:
            self.text_edit.setPlaceholderText("Match full cell (empty = no filter)")

    def _sync_case_button_appearance(self) -> None:
        if self._case_sensitive:
            self.case_btn.setText("Case")
        else:
            self.case_btn.setText("Ignore Case")
        _fc_set_toggle_active(self.case_btn, self._case_sensitive)

    def _on_case_clicked(self) -> None:
        self._case_sensitive = not self._case_sensitive
        self._sync_case_button_appearance()
        self.changed.emit()

    def _sync_partial_button_appearance(self) -> None:
        if self._partial_match:
            self.partial_btn.setText("Partial")
        else:
            self.partial_btn.setText("Exact")
        _fc_set_toggle_active(self.partial_btn, self._partial_match)

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
        _fc_install_card_shell(self, _FILTER_CARD_MIN_HEIGHT_CATEGORY)
        l = _fc_card_layout(self)
        self.cb = QComboBox()
        _fc_configure_column_combo(self.cb)
        self.cb.addItems(columns)
        self.cb.currentTextChanged.connect(self._on_column_changed)
        self._fc_init_enable_invert(
            "Invert category selection.",
            toolbar_min_width=_FC_TOOL_BTN_MIN_W,
        )
        self.all_btn = _fc_toolbar_button("All")
        self.all_btn.setToolTip("Check every category in the list.")
        self.all_btn.clicked.connect(self._select_all_categories)
        self.none_btn = _fc_toolbar_button("None")
        self.none_btn.setToolTip("Uncheck every category in the list.")
        self.none_btn.clicked.connect(self._select_no_categories)
        _fc_add_column_header_row(l, self.cb, lambda: self.removed.emit(self))
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.NoSelection)
        self.list_widget.setMaximumHeight(_FC_LIST_MAX)
        self.list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.list_widget.setMinimumWidth(0)
        # Cached for ``row_matches`` during a single ``apply_filters`` pass (avoid O(rows × list) work).
        self._category_checked_cache: frozenset[str] | None = None
        self._category_n_checkable_cache: int | None = None
        self.list_widget.itemChanged.connect(self._on_category_list_item_changed)
        l.addWidget(self.list_widget)
        _fc_add_bottom_tools_row(
            l,
            [self.enable_btn, self.invert_btn, self.all_btn, self.none_btn],
        )
        self._populate_list()

    def _bust_category_selection_cache(self) -> None:
        self._category_checked_cache = None
        self._category_n_checkable_cache = None

    def _on_category_list_item_changed(self, _it) -> None:
        self._bust_category_selection_cache()
        self.changed.emit()

    def _set_all_category_checkstates(self, state) -> None:
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(state)
        self.list_widget.blockSignals(False)
        self._bust_category_selection_cache()
        self.changed.emit()

    def _select_all_categories(self) -> None:
        self._set_all_category_checkstates(Qt.Checked)

    def _select_no_categories(self) -> None:
        self._set_all_category_checkstates(Qt.Unchecked)

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
        QTimer.singleShot(0, lambda: self._populate_list())

    def set_column(self, name: str) -> None:
        if name and self.cb.findText(name) >= 0:
            self.cb.blockSignals(True)
            self.cb.setCurrentText(name)
            self.cb.blockSignals(False)
            self._populate_list()

    def column_name(self) -> str:
        return (self.cb.currentText() or "").strip()

    def checked_values(self) -> frozenset[str]:
        self._ensure_category_filter_cache()
        return self._category_checked_cache or frozenset()

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
        ordered: list[str] = []
        cap = 2000
        ensure_sqlite = getattr(self.app, "_ensure_sqlite_store_current", None)
        store = getattr(self.app, "_sqlite_store", None)
        sqlite_ready = True
        if callable(ensure_sqlite):
            sqlite_ready = ensure_sqlite()
        if sqlite_ready and store is not None and prop in store.headers:
            ordered = store.distinct_values(prop, limit=cap + 1)
        else:
            seen: set[str] = set()
            for r in range(self.app._table_model.rowCount()):
                v = self.app._table_model.value_for_header(r, prop) or ""
                if v not in seen:
                    seen.add(v)
                    ordered.append(v)
            ordered.sort(key=lambda x: x.lower())
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
