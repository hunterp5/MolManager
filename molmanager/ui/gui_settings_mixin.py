"""Settings menu (GUI theme) for the main window."""

from __future__ import annotations

from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QAction, QActionGroup, QApplication, QFrame

from .theme import (
    THEME_DARK,
    THEME_LIGHT,
    apply_application_theme,
    current_theme_name,
    filter_card_stylesheet,
    filter_panel_stylesheet,
    load_saved_theme_name,
    save_theme_name,
)


class GuiSettingsMixin:
    """Settings → GUI: light/dark mode."""

    def _init_gui_settings(self) -> None:
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

    def _init_settings_menu(self, menubar) -> None:
        settings_menu = menubar.addMenu("&Settings")
        gui_menu = settings_menu.addMenu("&GUI")
        gui_menu.addAction(self._act_theme_light)
        gui_menu.addAction(self._act_theme_dark)

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
        if hasattr(self, "table") and self.table is not None:
            self.table.viewport().update()

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
        """Structure column cell background follows ``QPalette.Base`` (Fusion in both themes)."""
        if not hasattr(self, "table") or self._table_model is None:
            return
        from .compound_table_model import CompoundTableModel, StructureDelegate

        delg = StructureDelegate(self.table)
        delg.set_cell_background(self.palette().color(QPalette.Base))
        self.table.setItemDelegateForColumn(CompoundTableModel.STRUCTURE_COL, delg)
