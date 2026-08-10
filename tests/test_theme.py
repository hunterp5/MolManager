from __future__ import annotations

from molmanager.ui.theme import (
    THEME_DARK,
    THEME_GROOVY,
    THEME_LIGHT,
    current_theme_name,
    filter_card_stylesheet,
    load_saved_theme_name,
    palette_for_theme,
    save_theme_name,
)


def test_theme_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # No saved preference → light.
    from PyQt5.QtCore import QSettings

    from molmanager.ui.theme import _SETTINGS_APP, _SETTINGS_KEY_THEME, _SETTINGS_ORG

    QSettings(_SETTINGS_ORG, _SETTINGS_APP).remove(_SETTINGS_KEY_THEME)
    assert load_saved_theme_name() == THEME_LIGHT
    save_theme_name(THEME_DARK)
    assert load_saved_theme_name() == THEME_DARK
    save_theme_name(THEME_LIGHT)
    assert load_saved_theme_name() == THEME_LIGHT
    save_theme_name(THEME_GROOVY)
    assert load_saved_theme_name() == THEME_GROOVY


def test_filter_card_stylesheet_uses_palette_roles():
    qss = filter_card_stylesheet()
    assert "palette(base)" in qss.lower()
    assert "QFrame#FilterCard" in qss
    assert "QPushButton#fcToggle" in qss
    assert "QPushButton#fcToggle[fcActive=\"true\"]" in qss
    # Same rules for both themes — colors come from the application palette.
    assert filter_card_stylesheet(THEME_LIGHT) == filter_card_stylesheet(THEME_DARK)
    assert filter_card_stylesheet(THEME_GROOVY) == filter_card_stylesheet(THEME_LIGHT)


def test_apply_application_theme_sets_current(qapp):
    from PyQt5.QtWidgets import QApplication

    from molmanager.ui.theme import apply_application_theme

    apply_application_theme(QApplication.instance(), THEME_DARK)
    assert current_theme_name() == THEME_DARK
    apply_application_theme(QApplication.instance(), THEME_LIGHT)
    assert current_theme_name() == THEME_LIGHT
    apply_application_theme(QApplication.instance(), THEME_GROOVY)
    assert current_theme_name() == THEME_GROOVY


def test_both_themes_use_fusion_without_global_stylesheet(qapp):
    from PyQt5.QtWidgets import QApplication

    from molmanager.ui.theme import apply_application_theme

    app = QApplication.instance()
    for theme in (THEME_LIGHT, THEME_DARK, THEME_GROOVY):
        apply_application_theme(app, theme)
        assert app.style().objectName().lower() == "fusion"
        assert app.styleSheet() == ""


def test_groovy_palette_is_randomized():
    import random

    a = palette_for_theme(THEME_GROOVY, rng=random.Random(1))
    b = palette_for_theme(THEME_GROOVY, rng=random.Random(2))
    from PyQt5.QtGui import QPalette

    assert a.color(QPalette.Window) != b.color(QPalette.Window) or a.color(QPalette.Highlight) != b.color(
        QPalette.Highlight
    )


def test_refresh_open_windows_theme_calls_hook(qapp):
    from PyQt5.QtWidgets import QApplication, QDialog

    from molmanager.ui.theme import apply_application_theme, refresh_open_windows_theme

    class _ThemeDlg(QDialog):
        def __init__(self):
            super().__init__()
            self.refreshed = 0

        def refresh_theme(self) -> None:
            self.refreshed += 1

    dlg = _ThemeDlg()
    dlg.show()
    apply_application_theme(QApplication.instance(), THEME_DARK)
    assert dlg.refreshed >= 1
    before = dlg.refreshed
    refresh_open_windows_theme(QApplication.instance())
    assert dlg.refreshed == before + 1
    dlg.close()
