"""Settings menu (GUI theme and hotkeys) for the main window."""

from __future__ import annotations

from PyQt5.QtGui import QFont, QPalette
from PyQt5.QtWidgets import QAction, QActionGroup, QApplication, QDialog, QFrame

from .hotkeys import apply_hotkey_to_action
from .theme import (
    THEME_DARK,
    THEME_LIGHT,
    apply_application_font_pt,
    apply_application_theme,
    current_theme_name,
    default_app_font_pt,
    default_table_font_pt,
    filter_card_stylesheet,
    filter_panel_stylesheet,
    load_saved_app_font_pt,
    load_saved_table_font_pt,
    load_saved_theme_name,
    save_app_font_pt,
    save_table_font_pt,
    save_theme_name,
)


class GuiSettingsMixin:
    """Settings → GUI: light/dark mode."""

    def _init_gui_settings(self) -> None:
        self._hotkey_actions: dict[str, QAction] = {}
        self._app_font_pt = load_saved_app_font_pt()
        apply_application_font_pt(self._app_font_pt)
        self._gui_theme = load_saved_theme_name()
        apply_application_theme(QApplication.instance(), self._gui_theme)
        self._act_theme_light = QAction("Light Mode", self, checkable=True)
        self._act_theme_dark = QAction("Dark Mode", self, checkable=True)
        self._theme_action_group = QActionGroup(self)
        self._theme_action_group.setExclusive(True)
        self._theme_action_group.addAction(self._act_theme_light)
        self._theme_action_group.addAction(self._act_theme_dark)
        self._act_theme_light.triggered.connect(lambda: self._set_gui_theme(THEME_LIGHT))
        self._act_theme_dark.triggered.connect(lambda: self._set_gui_theme(THEME_DARK))
        self._sync_theme_menu_checks()
        self._refresh_filter_card_styles()
        self._table_font_pt = load_saved_table_font_pt()
        self._apply_table_font()

    def _bind_hotkey(self, action_id: str, action: QAction) -> QAction:
        """Register *action* for persistence and apply the saved shortcut."""
        self._hotkey_actions[action_id] = action
        apply_hotkey_to_action(action_id, action)
        return action

    def _apply_all_hotkeys(self) -> None:
        for action_id, action in self._hotkey_actions.items():
            apply_hotkey_to_action(action_id, action)

    def open_hotkeys_dialog(self) -> None:
        from .dialogs.hotkeys_dialog import HotkeysDialog

        dlg = HotkeysDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        self._apply_all_hotkeys()
        if hasattr(self, "status_label"):
            self.status_label.setText("Hotkeys updated.")

    def open_font_dialog(self) -> None:
        from .dialogs.font_settings import FontSettingsDialog

        prev_app_pt = int(getattr(self, "_app_font_pt", 0) or default_app_font_pt())
        prev_table_pt = int(getattr(self, "_table_font_pt", 0) or default_table_font_pt())
        dlg = FontSettingsDialog(prev_app_pt, prev_table_pt, self)
        dlg.app_font_size_previewed.connect(self._preview_app_font)
        dlg.table_font_size_previewed.connect(self._preview_table_font)
        if dlg.exec_() == QDialog.Accepted:
            self._set_app_font_pt(dlg.selected_app_point_size())
            self._set_table_font_pt(dlg.selected_table_point_size())
        else:
            self._set_app_font_pt(prev_app_pt, persist=False)
            self._set_table_font_pt(prev_table_pt, persist=False)
        if hasattr(self, "status_label"):
            self.status_label.setText(
                f"Font size — application: {self._app_font_pt} pt, table: {self._table_font_pt} pt"
            )

    def _init_settings_menu(self, menubar) -> None:
        settings_menu = menubar.addMenu("&Settings")
        gui_menu = settings_menu.addMenu("&GUI")
        gui_menu.addAction(self._act_theme_light)
        gui_menu.addAction(self._act_theme_dark)
        settings_menu.addSeparator()
        settings_menu.addAction(QAction("&Font…", self, triggered=self.open_font_dialog))
        settings_menu.addAction(QAction("&Hotkeys…", self, triggered=self.open_hotkeys_dialog))

    def _sync_theme_menu_checks(self) -> None:
        dark = current_theme_name() == THEME_DARK
        self._act_theme_light.setChecked(not dark)
        self._act_theme_dark.setChecked(dark)

    def _set_gui_theme(self, theme: str) -> None:
        theme = apply_application_theme(QApplication.instance(), theme)
        self._gui_theme = theme
        save_theme_name(theme)
        self._sync_theme_menu_checks()
        self._refresh_filter_card_styles()
        self._refresh_structure_delegate_theme()
        apply_application_font_pt(int(getattr(self, "_app_font_pt", 0) or default_app_font_pt()))
        self._apply_table_font()
        if hasattr(self, "table") and self.table is not None:
            self.table.viewport().update()

    def _preview_app_font(self, pt: int) -> None:
        self._app_font_pt = int(pt)
        apply_application_font_pt(self._app_font_pt)
        self._apply_table_font()

    def _set_app_font_pt(self, pt: int, *, persist: bool = True) -> None:
        self._app_font_pt = apply_application_font_pt(int(pt))
        if persist:
            save_app_font_pt(self._app_font_pt)
        self._apply_table_font()

    def _apply_table_font(self) -> None:
        """Set the table font point size on the view and its headers (theme-independent)."""
        table = getattr(self, "table", None)
        if table is None:
            return
        pt = int(getattr(self, "_table_font_pt", 0) or default_table_font_pt())
        font = QFont(table.font())
        font.setPointSize(pt)
        table.setFont(font)
        for header in (table.horizontalHeader(), table.verticalHeader()):
            if header is not None:
                header.setFont(font)
        table.viewport().update()

    def _preview_table_font(self, pt: int) -> None:
        self._table_font_pt = int(pt)
        self._apply_table_font()

    def _set_table_font_pt(self, pt: int, *, persist: bool = True) -> None:
        self._table_font_pt = int(pt)
        if persist:
            save_table_font_pt(self._table_font_pt)
        self._apply_table_font()

    def _refresh_filter_card_styles(self) -> None:
        panel = getattr(self, "f_panel", None)
        if panel is not None:
            panel.setStyleSheet(filter_panel_stylesheet())
        qss = filter_card_stylesheet()
        for filt in getattr(self, "filters", []):
            if isinstance(filt, QFrame) and filt.objectName() == "FilterCard":
                filt.setStyleSheet(qss)
                refresh = getattr(filt, "refresh_theme_styles", None)
                if callable(refresh):
                    refresh()

    def _refresh_structure_delegate_theme(self) -> None:
        """Structure column uses theme base for placeholders; rendered cells stay white."""
        if not hasattr(self, "table") or self._table_model is None:
            return
        from .compound_table_model import CompoundTableModel, StructureDelegate

        delg = getattr(self, "_structure_delegate", None)
        if not isinstance(delg, StructureDelegate):
            delg = StructureDelegate(self.table, self._table_model)
            self._structure_delegate = delg
        else:
            delg.set_compound_model(self._table_model)
        delg.set_cell_background(self.palette().color(QPalette.Base))
        self.table.setItemDelegateForColumn(CompoundTableModel.STRUCTURE_COL, delg)
