from __future__ import annotations

import re

from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt5.QtGui import QCursor, QFont, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QShortcut,
    QVBoxLayout,
    QWidget,
)

from rdkit import Chem

from ...workers import ExportWorker

from ..qt_widget_utils import make_window_minimizable
from .constants import SKETCH_ELEMENT_SYMBOLS, TOOLBAR_ELEMENT_SYMBOLS, WILDCARD_ELEMENT
from .widget import SketchWidget


def _sketcher_preferred_dialog_size() -> tuple[int, int]:
    scr = QApplication.primaryScreen()
    if scr is None:
        return (1280, 860)
    ag = scr.availableGeometry()
    w = max(1000, min(int(ag.width() * 0.74), ag.width() - 32))
    h = max(720, min(int(ag.height() * 0.76), ag.height() - 48))
    return (w, h)


class SketcherDialog(QDialog):
    def __init__(self, parent=None, initial_mol: Chem.Mol | None = None):
        super().__init__(parent)
        self.parent_app = parent
        if initial_mol is not None and not isinstance(initial_mol, Chem.Mol):
            initial_mol = None
        self._initial_mol = initial_mol
        self.setWindowTitle("Sketcher")
        self.resize(*_sketcher_preferred_dialog_size())
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        # Erase / Select: hidden widgets carry state for shortcuts and toggles (see canvas menu).
        self.tb_erase = QPushButton(self)
        self.tb_erase.setCheckable(True)
        self.tb_erase.setVisible(False)
        self.tb_erase.toggled.connect(self._toggle_erase)
        self.select_btn = QPushButton(self)
        self.select_btn.setCheckable(True)
        self.select_btn.setVisible(False)
        self.select_btn.toggled.connect(self._toggle_select)

        l = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addStretch()

        menubar = QMenuBar(self)
        file_menu = menubar.addMenu("File")
        export_act = QAction("Export sketch…", self)
        export_act.triggered.connect(self._export_sketch)
        file_menu.addAction(export_act)
        export_table_act = QAction("Export to Table", self)
        export_table_act.triggered.connect(self._add_to_table)
        export_table_act.setShortcut(QKeySequence("Ctrl+Shift+T"))
        export_table_act.setShortcutContext(Qt.WindowShortcut)
        file_menu.addAction(export_table_act)

        # Former Draw menu: right-click empty canvas (Mode entries listed first, unpacked).
        self._act_mode_draw = QAction("Draw (carbon tool)", self)
        self._act_mode_draw.setToolTip(
            "Leave erase/select/template and use carbon for drawing. "
            "Right-click empty canvas for templates, cleanup, and other draw commands."
        )
        self._act_mode_draw.triggered.connect(self._enter_draw_mode)
        self._act_mode_draw.setShortcut(QKeySequence("Ctrl+D"))
        self._act_mode_draw.setShortcutContext(Qt.WindowShortcut)

        self._act_mode_erase = QAction("Erase", self)
        self._act_mode_erase.setCheckable(True)
        self._act_mode_erase.setToolTip("Erase atoms and bonds (Ctrl+E).")
        self._act_mode_erase.setShortcut(QKeySequence("Ctrl+E"))
        self._act_mode_erase.setShortcutContext(Qt.WindowShortcut)
        self._act_mode_erase.toggled.connect(self._on_menu_mode_erase)

        self._act_mode_select = QAction("Select", self)
        self._act_mode_select.setCheckable(True)
        self._act_mode_select.setToolTip("Select and move atoms/bonds (Ctrl+T).")
        self._act_mode_select.setShortcut(QKeySequence("Ctrl+T"))
        self._act_mode_select.setShortcutContext(Qt.WindowShortcut)
        self._act_mode_select.toggled.connect(self._on_menu_mode_select)
        self.tb_erase.toggled.connect(self._sync_mode_menu_checks)
        self.select_btn.toggled.connect(self._sync_mode_menu_checks)

        self._act_canvas_group = QAction("Group", self)
        self._act_canvas_group.triggered.connect(self._shortcut_group)
        self._act_canvas_group.setShortcut(QKeySequence("Ctrl+G"))
        self._act_canvas_group.setShortcutContext(Qt.WindowShortcut)

        self._act_canvas_ungroup = QAction("Ungroup", self)
        self._act_canvas_ungroup.triggered.connect(self._shortcut_ungroup)
        self._act_canvas_ungroup.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self._act_canvas_ungroup.setShortcutContext(Qt.WindowShortcut)

        self._act_canvas_clear = QAction("Clear sketch", self)
        self._act_canvas_clear.triggered.connect(self._clear_sketch)
        self._act_canvas_clear.setShortcut(QKeySequence("Ctrl+Shift+K"))
        self._act_canvas_clear.setShortcutContext(Qt.WindowShortcut)

        self._act_canvas_cleanup = QAction("Clean Up", self)
        self._act_canvas_cleanup.triggered.connect(self._on_cleanup_layout)
        self._act_canvas_cleanup.setShortcut(QKeySequence("Ctrl+K"))
        self._act_canvas_cleanup.setShortcutContext(Qt.WindowShortcut)

        self._act_canvas_add_hs = QAction("Show implicit hydrogens for entire structure…", self)
        self._act_canvas_add_hs.setToolTip(
            "Expand the sketch so all implicit hydrogens become explicit H atoms and bonds (RDKit AddHs). "
            "Heavy atoms stay pinned while hydrogens are re-depicted for better angles and spacing."
        )
        self._act_canvas_add_hs.triggered.connect(self._on_add_explicit_hydrogens)

        view_menu = menubar.addMenu("View")
        zoom_in_act = QAction("Zoom in", self)
        zoom_in_act.setToolTip("Zoom in (bonds and labels scale together; does not change the sketch coordinates).")
        zoom_in_act.triggered.connect(self._on_view_zoom_in)
        zoom_in_act.setShortcut(QKeySequence.ZoomIn)
        zoom_in_act.setShortcutContext(Qt.WindowShortcut)
        view_menu.addAction(zoom_in_act)
        zoom_out_act = QAction("Zoom out", self)
        zoom_out_act.setToolTip("Zoom out (bonds and labels scale together; does not change the sketch coordinates).")
        zoom_out_act.triggered.connect(self._on_view_zoom_out)
        zoom_out_act.setShortcut(QKeySequence.ZoomOut)
        zoom_out_act.setShortcutContext(Qt.WindowShortcut)
        view_menu.addAction(zoom_out_act)
        view_menu.addSeparator()
        fit_v_act = QAction("Fit structure to window", self)
        fit_v_act.setToolTip("Scale and center so the whole sketch fits in the canvas with margin.")
        fit_v_act.triggered.connect(self._on_view_fit_structure)
        view_menu.addAction(fit_v_act)

        tools = menubar.addMenu("Tools")

        elements_menu = tools.addMenu("Elements")
        elements_menu.setToolTip("Choose any supported element for drawing (toolbar shows a med-chem subset).")
        for el in SKETCH_ELEMENT_SYMBOLS:
            el_act = QAction(el, self)
            el_act.triggered.connect(lambda _=False, e=el: self._select_element_tool(e))
            elements_menu.addAction(el_act)
        wild_act = QAction("Wildcard (*)", self)
        wild_act.triggered.connect(self._select_wildcard_element_tool)
        elements_menu.addSeparator()
        elements_menu.addAction(wild_act)

        copy_act = QAction("Copy SMILES", self)
        copy_act.triggered.connect(self._copy_smiles)
        copy_act.setShortcut(QKeySequence("Ctrl+Shift+C"))
        tools.addAction(copy_act)
        copy_smarts_act = QAction("Copy SMARTS", self)
        copy_smarts_act.triggered.connect(self._copy_smarts)
        tools.addAction(copy_smarts_act)
        tools.addSeparator()
        center_mol_act = QAction("Center molecule", self)
        center_mol_act.setToolTip("Move the whole sketch so it is centered in the canvas (undo: Ctrl+Z).")
        center_mol_act.triggered.connect(self._on_center_molecule)
        tools.addAction(center_mol_act)

        l.setMenuBar(menubar)
        l.addLayout(top)

        main_h = QHBoxLayout()

        def _toolbar_header(text: str) -> QLabel:
            lab = QLabel(text)
            lab.setStyleSheet(
                "color: palette(mid); font-size: 11px; font-weight: 600; letter-spacing: 0.4px; padding-top: 2px;"
            )
            return lab

        def _toolbar_rule() -> QFrame:
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setFrameShadow(QFrame.Sunken)
            line.setMaximumHeight(1)
            return line

        toolbar_outer = QVBoxLayout()
        toolbar_outer.setSpacing(8)
        toolbar_outer.setContentsMargins(8, 6, 8, 10)

        # --- Mode (Erase / Select / Draw; shortcuts + right-click empty canvas menu) ---
        toolbar_outer.addWidget(_toolbar_header("Mode"))
        self.tb_clear = QPushButton("Clear")
        self.tb_clear.setMinimumHeight(28)
        self.tb_clear.setToolTip("Clear the sketch (also in the right-click empty-canvas menu).")
        self.tb_clear.clicked.connect(self._clear_sketch)
        toolbar_outer.addWidget(self.tb_clear)

        toolbar_outer.addWidget(_toolbar_rule())

        # --- Charge ---
        self.charge_plus = QPushButton("+")
        self.charge_plus.setCheckable(True)
        self.charge_plus.setMinimumHeight(28)
        self.charge_plus.clicked.connect(lambda checked: self._toggle_charge(1 if checked else None))
        self.charge_minus = QPushButton("-")
        self.charge_minus.setCheckable(True)
        self.charge_minus.setMinimumHeight(28)
        self.charge_minus.clicked.connect(lambda checked: self._toggle_charge(-1 if checked else None))
        toolbar_outer.addWidget(_toolbar_header("Charge"))
        row_ch = QHBoxLayout()
        row_ch.setSpacing(6)
        row_ch.addWidget(self.charge_plus, 1)
        row_ch.addWidget(self.charge_minus, 1)
        toolbar_outer.addLayout(row_ch)

        toolbar_outer.addWidget(_toolbar_rule())

        # --- Elements (med-chem toolbar subset; full list under Tools → Elements; fixed grid, no scroll) ---
        self.element_buttons: list[QPushButton] = []
        self._element_btn_by_symbol: dict[str, QPushButton] = {}
        self._element_button_group = QButtonGroup(self)
        self._element_button_group.setExclusive(True)
        toolbar_outer.addWidget(_toolbar_header("Elements"))
        el_grid = QGridLayout()
        el_grid.setHorizontalSpacing(4)
        el_grid.setVerticalSpacing(4)
        el_ncols = 5
        btn_font = QFont("Sans", 8, QFont.Bold)
        btn_font.setStyleHint(QFont.SansSerif)
        for i, el in enumerate(TOOLBAR_ELEMENT_SYMBOLS):
            b = QPushButton(el)
            b.setCheckable(True)
            b.setProperty("sketch_element", el)
            b.setFont(btn_font)
            b.setFixedSize(28, 26)
            b.setStyleSheet("padding: 0px;")
            row, col = i // el_ncols, i % el_ncols
            if el == "C":
                b.setToolTip(
                    "Carbon: click empty space to place C; click another C to extend a chain; "
                    "click any other atom to replace it with carbon. More elements: Tools → Elements."
                )
            else:
                b.setToolTip(f"Place {el}. More elements: Tools → Elements.")
            b.clicked.connect(lambda checked, e=el: self._on_element_tool_clicked(e, checked))
            self._element_button_group.addButton(b)
            el_grid.addWidget(b, row, col)
            self.element_buttons.append(b)
            self._element_btn_by_symbol[el] = b
        self.tb_wildcard = QPushButton("*")
        self.tb_wildcard.setCheckable(True)
        self.tb_wildcard.setFont(btn_font)
        self.tb_wildcard.setToolTip(
            "Wildcard atom: SMARTS query over selected elements. Right-click a wildcard to edit choices. "
            "Tools → Elements for the full palette."
        )
        self.tb_wildcard.setFixedSize(28, 26)
        self.tb_wildcard.setStyleSheet("padding: 0px;")
        self.tb_wildcard.toggled.connect(self._on_wildcard_tool_toggled)
        wrow = (len(TOOLBAR_ELEMENT_SYMBOLS) + el_ncols - 1) // el_ncols
        el_grid.addWidget(self.tb_wildcard, wrow, 0, 1, el_ncols)
        toolbar_outer.addLayout(el_grid)

        toolbar_outer.addWidget(_toolbar_rule())

        # --- Bond stereo ---
        self._bond_stereo_group = QButtonGroup(self)
        self._bond_stereo_group.setExclusive(True)
        self.bond_plain = QPushButton("Plain")
        self.bond_plain.setCheckable(True)
        self.bond_plain.setChecked(True)
        self.bond_plain.setMinimumHeight(26)
        self.bond_plain.clicked.connect(lambda _=False: self._on_bond_stereo_tool(0))
        self._bond_stereo_group.addButton(self.bond_plain)
        self.bond_wedge = QPushButton("Wedge")
        self.bond_wedge.setCheckable(True)
        self.bond_wedge.setMinimumHeight(26)
        self.bond_wedge.clicked.connect(lambda _=False: self._on_bond_stereo_tool(1))
        self._bond_stereo_group.addButton(self.bond_wedge)
        self.bond_hash = QPushButton("Hash")
        self.bond_hash.setCheckable(True)
        self.bond_hash.setMinimumHeight(26)
        self.bond_hash.clicked.connect(lambda _=False: self._on_bond_stereo_tool(2))
        self._bond_stereo_group.addButton(self.bond_hash)
        toolbar_outer.addWidget(_toolbar_header("Bond"))
        toolbar_outer.addWidget(self.bond_plain)
        toolbar_outer.addWidget(self.bond_wedge)
        toolbar_outer.addWidget(self.bond_hash)

        toolbar_outer.addStretch()

        toolbar_widget = QWidget()
        toolbar_widget.setObjectName("SketcherToolbarPanel")
        toolbar_widget.setStyleSheet(
            "#SketcherToolbarPanel { background-color: palette(window); border-right: 1px solid palette(midlight); }"
        )
        toolbar_widget.setFixedWidth(168)
        toolbar_widget.setLayout(toolbar_outer)
        main_h.addWidget(toolbar_widget)

        self.canvas = SketchWidget(self)
        self.canvas.select_mode = False
        self.canvas.setToolTip("Right-click empty canvas for templates, modes, cleanup, and zoom-related commands in the menu bar.")
        self.canvas.setFocus()
        main_h.addWidget(self.canvas, 1)
        l.addLayout(main_h)

        self.sketch_status = QLabel("")
        self.sketch_status.setWordWrap(True)
        self.sketch_status.setStyleSheet("color: palette(mid);")
        l.addWidget(self.sketch_status)
        self.canvas.sketchChanged.connect(self._update_sketch_status)
        self.tb_erase.blockSignals(True)
        self.tb_erase.setChecked(False)
        self.tb_erase.blockSignals(False)
        self.select_btn.blockSignals(True)
        self.select_btn.setChecked(True)
        self.select_btn.blockSignals(False)
        self._toggle_select(True)
        self._update_sketch_status()

        if self._initial_mol is not None:
            QTimer.singleShot(0, self._apply_initial_mol)

        self._sync_mode_menu_checks()

        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.WidgetWithChildrenShortcut)
        esc.activated.connect(self._escape_asks_close)

        sc_copy_sel = QShortcut(QKeySequence.Copy, self)
        sc_copy_sel.setContext(Qt.WindowShortcut)
        sc_copy_sel.activated.connect(self._shortcut_copy_selection)
        sc_paste_sel = QShortcut(QKeySequence.Paste, self)
        sc_paste_sel.setContext(Qt.WindowShortcut)
        sc_paste_sel.activated.connect(self._shortcut_paste_selection)
        self._parent_delete_action = None
        self._parent_delete_was_enabled = False
        self._sketch_key_filters_installed = False
        make_window_minimizable(self)

    @staticmethod
    def _is_sketch_delete_key(event) -> bool:
        if event.key() not in (Qt.Key_Delete, Qt.Key_Backspace):
            return False
        mods = event.modifiers()
        return not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))

    def _resolve_parent_delete_action(self):
        if self._parent_delete_action is not None:
            return self._parent_delete_action
        parent = self.parent()
        if parent is not None and hasattr(parent, "_hotkey_actions"):
            self._parent_delete_action = parent._hotkey_actions.get("edit.delete_selection")
        return self._parent_delete_action

    def _set_parent_delete_action_blocked(self, blocked: bool) -> None:
        act = self._resolve_parent_delete_action()
        if act is None:
            return
        if blocked:
            if act.isEnabled():
                self._parent_delete_was_enabled = True
                act.setEnabled(False)
        elif self._parent_delete_was_enabled:
            act.setEnabled(True)
            self._parent_delete_was_enabled = False

    def _install_sketch_key_filters(self) -> None:
        if self._sketch_key_filters_installed:
            return
        for widget in (self, *self.findChildren(QWidget)):
            widget.installEventFilter(self)
        self._sketch_key_filters_installed = True

    def _remove_sketch_key_filters(self) -> None:
        if not self._sketch_key_filters_installed:
            return
        for widget in (self, *self.findChildren(QWidget)):
            widget.removeEventFilter(self)
        self._sketch_key_filters_installed = False

    def eventFilter(self, obj, event) -> bool:  # noqa: ARG002
        if not self.isVisible():
            return False
        if event.type() == QEvent.ShortcutOverride and self._is_sketch_delete_key(event):
            event.accept()
            return False
        if event.type() == QEvent.KeyPress and self._is_sketch_delete_key(event):
            self.canvas._handle_delete_key()
            return True
        return False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._set_parent_delete_action_blocked(True)
        self._install_sketch_key_filters()

    def hideEvent(self, event) -> None:
        self._set_parent_delete_action_blocked(False)
        self._remove_sketch_key_filters()
        super().hideEvent(event)

    def _sync_mode_menu_checks(self) -> None:
        if not getattr(self, "_act_mode_erase", None):
            return
        self._act_mode_erase.blockSignals(True)
        self._act_mode_erase.setChecked(self.tb_erase.isChecked())
        self._act_mode_erase.blockSignals(False)
        self._act_mode_select.blockSignals(True)
        self._act_mode_select.setChecked(self.select_btn.isChecked())
        self._act_mode_select.blockSignals(False)

    def show_sketch_canvas_menu(self, global_pos: QPoint) -> None:
        """Templates, modes, and cleanup (formerly the Draw menu). Right-click empty canvas."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.aboutToShow.connect(self._sync_mode_menu_checks)
        menu.addAction(self._act_mode_draw)
        menu.addSeparator()
        menu.addAction(self._act_mode_erase)
        menu.addAction(self._act_mode_select)
        menu.addSeparator()
        tpl_menu = menu.addMenu("Templates")
        tpl_menu.setToolTipsVisible(True)
        self._populate_templates_menu(tpl_menu)
        menu.addSeparator()
        menu.addAction(self._act_canvas_group)
        menu.addAction(self._act_canvas_ungroup)
        menu.addAction(self._act_canvas_clear)
        menu.addAction(self._act_canvas_cleanup)
        menu.addAction(self._act_canvas_add_hs)
        menu.exec_(global_pos)

    def _on_view_zoom_in(self) -> None:
        self.canvas.zoom_about_viewport_center(1.15, True)
        self._update_sketch_status()

    def _on_view_zoom_out(self) -> None:
        self.canvas.zoom_about_viewport_center(1.0 / 1.15, True)
        self._update_sketch_status()

    def _on_view_fit_structure(self) -> None:
        self.canvas.fit_sketch_to_viewport()
        self._update_sketch_status()

    def _on_menu_mode_erase(self, checked: bool) -> None:
        prev = self.tb_erase.isChecked()
        self.tb_erase.blockSignals(True)
        if prev != checked:
            self.tb_erase.setChecked(checked)
        self.tb_erase.blockSignals(False)
        if prev != checked:
            self._toggle_erase(checked)

    def _on_menu_mode_select(self, checked: bool) -> None:
        prev = self.select_btn.isChecked()
        self.select_btn.blockSignals(True)
        if prev != checked:
            self.select_btn.setChecked(checked)
        self.select_btn.blockSignals(False)
        if prev != checked:
            self._toggle_select(checked)

    def _select_element_tool(self, el: str) -> None:
        self._on_element_tool_clicked(el, True)

    def _select_wildcard_element_tool(self) -> None:
        if getattr(self, "tb_wildcard", None) is None:
            return
        self.tb_wildcard.blockSignals(True)
        self.tb_wildcard.setChecked(True)
        self.tb_wildcard.blockSignals(False)
        self._on_wildcard_tool_toggled(True)

    def _enter_draw_mode(self) -> None:
        """Leave erase/select/template, choose carbon, and focus the canvas for drawing."""
        self._leave_special_modes_for_drawing()
        self.canvas.active_template = None
        if getattr(self, "tb_wildcard", None) is not None:
            self.tb_wildcard.blockSignals(True)
            self.tb_wildcard.setChecked(False)
            self.tb_wildcard.blockSignals(False)
        self._select_default_element_tool()
        self.canvas.setFocus()
        self._update_sketch_status()
        self._sync_mode_menu_checks()

    def _shortcut_copy_selection(self) -> None:
        if self.canvas.copy_selection_to_clipboard():
            self.sketch_status.setText("Copied selection to clipboard (paste with Ctrl+V).")
        else:
            self.sketch_status.setText("Turn on Select, pick atoms/bonds, then Ctrl+C to copy.")

    def _shortcut_paste_selection(self) -> None:
        anchor = self.canvas.mapFromGlobal(QCursor.pos())
        if self.canvas.paste_from_clipboard(anchor):
            self._update_sketch_status()
        else:
            self.sketch_status.setText("Clipboard has no sketch selection (use Ctrl+C in Select mode first).")

    def _populate_templates_menu(self, tpl_menu: QMenu) -> None:
        def add_section(title: str, pairs: list[tuple[str, str]]) -> None:
            tpl_menu.addSection(title)
            for label, key in pairs:
                act = QAction(label, self)
                act.triggered.connect(lambda _=False, k=key: self._select_template_from_menu(k))
                tpl_menu.addAction(act)

        add_section(
            "Carbocycles",
            [
                ("Benzene", "Benzene"),
                ("Cyclopropane", "Cyclopropane"),
                ("Cyclobutane", "Cyclobutane"),
                ("Cyclopentyl", "Cyclopentyl"),
                ("Cyclohexyl", "Cyclohexyl"),
            ],
        )
        add_section(
            "Nitrogen heterocycles",
            [
                ("Pyridine", "Pyridine"),
                ("Pyrimidine", "Pyrimidine"),
                ("Pyrazine", "Pyrazine"),
                ("Pyridazine", "Pyridazine"),
                ("1,3,5-Triazine", "Triazine"),
                ("Pyrrole", "Pyrrole"),
                ("Imidazole", "Imidazole"),
                ("Pyrazole", "Pyrazole"),
                ("1,2,4-Triazole", "Triazole_124"),
                ("1,2,3-Triazole", "Triazole_123"),
                ("Piperidine", "Piperidine"),
                ("Piperazine", "Piperazine"),
                ("Morpholine", "Morpholine"),
            ],
        )
        add_section(
            "Oxygen heterocycles",
            [
                ("Furan", "Furan"),
                ("Oxazole", "Oxazole"),
                ("Isoxazole", "Isoxazole"),
                ("Tetrahydrofuran (THF)", "THF"),
                ("Oxetane", "Oxetane"),
                ("1,4-Dioxane", "Dioxane"),
                ("1,3-Dioxolane", "Dioxolane"),
                ("1,3,4-Oxadiazole", "Oxadiazole"),
            ],
        )
        add_section(
            "Sulfur heterocycles",
            [
                ("Thiophene", "Thiophene"),
                ("Thiazole", "Thiazole"),
                ("Isothiazole", "Isothiazole"),
                ("Thietane", "Thietane"),
                ("Thiane (tetrahydrothiopyran)", "Thiane"),
                ("1,3,4-Thiadiazole", "Thiadiazole"),
            ],
        )

    def _leave_special_modes_for_drawing(self) -> None:
        """Exit Select and Erase so drawing tools (element/template) apply."""
        if self.tb_erase.isChecked():
            self.tb_erase.blockSignals(True)
            self.tb_erase.setChecked(False)
            self.tb_erase.blockSignals(False)
        if self.select_btn.isChecked():
            self.select_btn.blockSignals(True)
            self.select_btn.setChecked(False)
            self.select_btn.blockSignals(False)
        self.canvas.erase_mode = False
        self.canvas.select_mode = False
        self.canvas.setCursor(Qt.ArrowCursor)
        self.canvas.selected_nodes = []
        self.canvas.selected_bond_indices = set()
        self.canvas._selection_rect = None
        self.canvas._selecting = False
        self.canvas._release_marquee_mouse_grab_if_any()
        self.canvas._maybe_move = False
        self.canvas._moving = False
        self._reset_bond_stereo_toolbar()
        self.canvas.update()
        self._sync_mode_menu_checks()

    def _reset_bond_stereo_toolbar(self) -> None:
        self.canvas.active_bond_stereo = 0
        if getattr(self, "bond_plain", None) is None:
            return
        for btn, v in ((self.bond_plain, 0), (self.bond_wedge, 1), (self.bond_hash, 2)):
            btn.blockSignals(True)
            btn.setChecked(v == 0)
            btn.blockSignals(False)

    def _on_bond_stereo_tool(self, val: int) -> None:
        self.canvas.active_bond_stereo = val
        for btn, v in ((self.bond_plain, 0), (self.bond_wedge, 1), (self.bond_hash, 2)):
            btn.blockSignals(True)
            btn.setChecked(v == val)
            btn.blockSignals(False)

    def _uncheck_element_buttons_clear_place(self) -> None:
        for b in self.element_buttons:
            b.blockSignals(True)
            b.setChecked(False)
            b.blockSignals(False)
        if getattr(self, "tb_wildcard", None) is not None:
            self.tb_wildcard.blockSignals(True)
            self.tb_wildcard.setChecked(False)
            self.tb_wildcard.blockSignals(False)
        self.canvas.place_element = None

    def _select_template_from_menu(self, name: str) -> None:
        self._leave_special_modes_for_drawing()
        self._uncheck_element_buttons_clear_place()
        self.canvas.active_template = name

    def _export_sketch(self) -> None:
        smi = self.canvas.to_smiles().strip()
        if not smi:
            QMessageBox.warning(self, "Export", "No valid structure to export from the sketch.")
            return
        mol = Chem.MolFromSmiles(smi) or Chem.MolFromSmarts(smi)
        if mol is None:
            QMessageBox.warning(self, "Export", "RDKit could not build a molecule from the sketch (SMILES/SMARTS).")
            return
        app = self.parent_app
        if app is None or not hasattr(app, "threadpool") or not hasattr(app, "signals"):
            QMessageBox.warning(self, "Export", "Main application is not available for export.")
            return
        f_filter = "SDF (*.sdf);;Molfile (*.mol);;SMILES (*.smi)"
        path, sel_f = QFileDialog.getSaveFileName(self, "Export Sketch", "", f_filter)
        if not path or not sel_f:
            return
        m = re.search(r"\((.*)\)", sel_f)
        if not m:
            return
        ext = m.group(1).replace("*", "")
        if not path.endswith(ext):
            path += ext
        oid = 0
        mols = {oid: mol}
        heads = ["ID_HIDDEN", "Structure", "SMILES"]
        data = {oid: {"SMILES": smi}}
        app.process_queue.enqueue(
            f"Export sketch: {path}",
            lambda ev, p=path, e=ext, m=mols, h=heads, d=data, s=app.signals: ExportWorker(
                p, e, m, h, d, s, cancel_event=ev
            ),
        )

    def _escape_asks_close(self) -> None:
        self.close()

    def _on_element_tool_clicked(self, el: str, checked: bool) -> None:
        if not checked:
            return
        if getattr(self, "tb_wildcard", None) is not None:
            self.tb_wildcard.blockSignals(True)
            self.tb_wildcard.setChecked(False)
            self.tb_wildcard.blockSignals(False)
        self._leave_special_modes_for_drawing()
        self.canvas.active_template = None
        self.canvas.place_element = el
        for b in self.element_buttons:
            bel = b.property("sketch_element")
            b.blockSignals(True)
            b.setChecked(bel == el)
            b.blockSignals(False)

    def _on_wildcard_tool_toggled(self, on: bool) -> None:
        if on:
            for b in self.element_buttons:
                b.blockSignals(True)
                b.setChecked(False)
                b.blockSignals(False)
            self._leave_special_modes_for_drawing()
            self.canvas.active_template = None
            self.canvas.place_element = WILDCARD_ELEMENT
        elif self.canvas.place_element == WILDCARD_ELEMENT:
            self._select_default_element_tool()

    def _select_default_element_tool(self) -> None:
        self.canvas.place_element = "C"
        if getattr(self, "tb_wildcard", None) is not None:
            self.tb_wildcard.blockSignals(True)
            self.tb_wildcard.setChecked(False)
            self.tb_wildcard.blockSignals(False)
        bc = self._element_btn_by_symbol.get("C")
        if bc is not None:
            bc.setChecked(True)

    def _ensure_main_table_for_sketch_import(self, app) -> None:
        """Allow sketcher add before any file load: minimal columns + visible table."""
        if app.headers and app._table_model.columnCount() >= 2:
            return
        app.headers = ["ID_HIDDEN", "Structure", "SMILES"]
        app.table.setSortingEnabled(False)
        app._table_model.clear_rows()
        app._table_model.set_headers(list(app.headers))
        app.table.setColumnHidden(0, True)
        if hasattr(app, "_table_stack"):
            app._table_stack.setCurrentIndex(1)

    def _append_molecules_from_smiles_parts(self, parts: list[str]) -> int:
        """Insert one table row per SMILES string. Returns number added."""
        app = self.parent_app
        self._ensure_main_table_for_sketch_import(app)
        n_added = 0
        smiles_col = app.headers.index("SMILES") if "SMILES" in app.headers else None
        for smi in parts:
            smi = (smi or "").strip()
            if not smi:
                continue
            mol = Chem.MolFromSmiles(smi) or Chem.MolFromSmarts(smi)
            if mol is None:
                continue
            oid = app.next_oid
            app.next_oid += 1
            app.mols[oid] = mol
            cells: dict[str, str] = {}
            if smiles_col is not None:
                cells[app.headers[smiles_col]] = smi
            app._table_model.append_row(oid, cells)
            app.start_render_worker(oid, mol)
            n_added += 1
        if n_added:
            app.status_label.setText(f"Added {n_added} molecule(s) from sketcher")
            if hasattr(app, "calculate_global_bounds"):
                app.calculate_global_bounds()
            if hasattr(app, "apply_filters"):
                app.apply_filters()
        return n_added

    def _update_sketch_status(self) -> None:
        n_atoms = len(self.canvas.nodes)
        if n_atoms == 0:
            self.sketch_status.setText("Empty sketch")
            return
        parts = self.canvas.fragment_smiles_parts()
        n_export = len(parts)
        phy = self.canvas.fragment_count()
        if phy > n_export >= 1:
            if getattr(self.canvas, "_group_bundle_is_salt", False):
                frag_txt = (
                    f"{phy} drawn fragment(s); {n_export} row(s) for Add to table "
                    "(grouped as a salt: cations before anions in SMILES)"
                )
            else:
                frag_txt = (
                    f"{phy} drawn fragment(s); {n_export} row(s) for Add to table "
                    "(grouped: multiple structures in one SMILES entry; not a salt)"
                )
        elif phy <= 1:
            frag_txt = "One connected structure"
        else:
            frag_txt = f"{phy} separate structures (Add to table: one row per fragment unless grouped)"
        smi = ".".join(parts) if parts else ""
        cip = self.canvas._format_cip_chiral_summary()
        ez = self.canvas._format_alkene_ez_summary()
        has_w = self.canvas.sketch_has_wildcards()
        if has_w:
            disp = self.canvas.to_smarts().strip() or smi
            tag = "SMARTS"
        else:
            disp = smi
            tag = "SMILES"
        if disp:
            preview = disp if len(disp) <= 96 else disp[:93] + "..."
            self.sketch_status.setText(f"{frag_txt} · {tag}: {preview}{cip}{ez}")
        else:
            self.sketch_status.setText(f"{frag_txt} · {tag} not available (check bonding/valence){cip}{ez}")

    def _shortcut_group(self) -> None:
        if not self.canvas.select_mode:
            QMessageBox.information(
                self,
                "Group",
                "Turn on Select mode, select atoms from at least two disconnected structures, then press Ctrl+G.",
            )
            return
        self.canvas._run_group_selection_menu()

    def _shortcut_ungroup(self) -> None:
        ok = self.canvas.ungroup_for_export()
        self._update_sketch_status()
        if not ok:
            t = self.sketch_status.text()
            self.sketch_status.setText(f"{t} · No export group to remove (Ctrl+G groups fragments).")

    def _toggle_erase(self, checked: bool):
        if checked and self.select_btn.isChecked():
            self.select_btn.blockSignals(True)
            self.select_btn.setChecked(False)
            self.select_btn.blockSignals(False)
            self.canvas.selected_nodes = []
            self.canvas.selected_bond_indices = set()
            self.canvas._selection_rect = None
            self.canvas._selecting = False
            self.canvas._release_marquee_mouse_grab_if_any()
            self.canvas._maybe_move = False
            self.canvas._moving = False
            self.canvas.select_mode = False
        self.canvas.erase_mode = checked
        if checked:
            self.canvas.setCursor(Qt.CrossCursor)
            self.canvas.place_element = None
            self.canvas.active_template = None
            self._uncheck_element_buttons_clear_place()
        else:
            self.canvas.setCursor(Qt.ArrowCursor)
            self._select_default_element_tool()

    def _toggle_select(self, checked: bool):
        if checked and self.tb_erase.isChecked():
            self.tb_erase.blockSignals(True)
            self.tb_erase.setChecked(False)
            self.tb_erase.blockSignals(False)
            self.canvas.erase_mode = False
            self.canvas.setCursor(Qt.ArrowCursor)
        self.canvas.select_mode = checked
        if checked:
            self.canvas.place_element = None
            self.canvas.active_template = None
            self._uncheck_element_buttons_clear_place()
            try:
                self.canvas._refresh_hover_from_cursor()
            except Exception:
                self.canvas.setCursor(Qt.ArrowCursor)
        else:
            self.canvas.selected_nodes = []
            self.canvas.selected_bond_indices = set()
            self.canvas._selection_rect = None
            self.canvas._selecting = False
            self.canvas._release_marquee_mouse_grab_if_any()
            self.canvas._maybe_move = False
            self.canvas._moving = False
            self.canvas.setCursor(Qt.ArrowCursor)
            self._select_default_element_tool()

    def _toggle_charge(self, val: int | None):
        try:
            self.canvas.active_charge = val
        except Exception:
            pass
        if val == 1:
            self.charge_minus.setChecked(False)
        if val == -1:
            self.charge_plus.setChecked(False)

    def _clear_sketch(self) -> None:
        self.canvas.clear()

    def _on_center_molecule(self) -> None:
        self.canvas.center_sketch_in_viewport(True)
        self._update_sketch_status()

    def _on_add_explicit_hydrogens(self) -> None:
        ok, err = self.canvas.add_explicit_hydrogens_from_implicit()
        if not ok:
            QMessageBox.information(self, "Add hydrogens", err)
            return
        self._leave_special_modes_for_drawing()
        self._select_default_element_tool()
        self._update_sketch_status()

    def _apply_initial_mol(self) -> None:
        mol = self._initial_mol
        self._initial_mol = None
        if mol is None or not isinstance(mol, Chem.Mol):
            return
        self.load_structure_from_mol(mol, confirm_if_nonempty=False)

    def load_structure_from_mol(self, mol: Chem.Mol | None, confirm_if_nonempty: bool = True) -> None:
        """Load an RDKit molecule into the canvas (optionally confirm if the sketch is non-empty)."""
        if mol is None or not isinstance(mol, Chem.Mol):
            return
        try:
            m = Chem.Mol(mol)
        except Exception:
            QMessageBox.warning(self, "Sketcher", "Could not copy this structure for editing.")
            return
        if confirm_if_nonempty and self.canvas.to_smiles().strip():
            res = QMessageBox.question(
                self,
                "Replace sketch",
                "Replace the current sketch with this structure?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if res != QMessageBox.Yes:
                return
        c = self.canvas.rect().center()
        center = c if self.canvas.rect().width() > 8 and self.canvas.rect().height() > 8 else None
        if not self.canvas.load_from_rdkit_mol(m, center=center):
            QMessageBox.warning(
                self,
                "Sketcher",
                "Could not build a 2D layout for this structure. It may be unsupported or invalid.",
            )
            return
        self.tb_erase.blockSignals(True)
        self.tb_erase.setChecked(False)
        self.tb_erase.blockSignals(False)
        self.select_btn.blockSignals(True)
        self.select_btn.setChecked(True)
        self.select_btn.blockSignals(False)
        self._toggle_select(True)
        self._update_sketch_status()

    def _on_cleanup_layout(self) -> None:
        ok = self.canvas.cleanup_layout_2d()
        if not ok:
            QMessageBox.information(
                self,
                "Clean Up",
                "Could not re-layout the structure. Try fixing valence or connectivity issues first.",
            )
        self._update_sketch_status()

    def _copy_smiles(self):
        smi = self.canvas.to_smiles()
        if smi:
            QApplication.clipboard().setText(smi)
            self.sketch_status.setText(f"Copied SMILES to clipboard ({len(smi)} characters).")
        else:
            self.sketch_status.setText("Could not copy — no valid SMILES for the sketch.")

    def _copy_smarts(self) -> None:
        smt = self.canvas.to_smarts().strip()
        if smt:
            QApplication.clipboard().setText(smt)
            self.sketch_status.setText(f"Copied SMARTS to clipboard ({len(smt)} characters).")
        else:
            self.sketch_status.setText("Could not copy SMARTS (empty sketch or invalid structure).")

    def _add_to_table(self):
        parts = self.canvas.fragment_smiles_parts()
        app = self.parent_app

        def _main_status(msg: str) -> None:
            if app is not None and hasattr(app, "status_label"):
                app.status_label.setText(msg)

        if not parts:
            msg = "Sketcher: could not build a valid structure to add to the table (check bonding/valence)."
            _main_status(msg)
            self.sketch_status.setText(msg)
            return
        n = self._append_molecules_from_smiles_parts(parts)
        if n == 0:
            msg = "Sketcher: could not parse fragments when adding to the table."
            _main_status(msg)
            self.sketch_status.setText(msg)
            return
        self._update_sketch_status()

    def closeEvent(self, event):
        self._set_parent_delete_action_blocked(False)
        self._remove_sketch_key_filters()
        event.accept()

