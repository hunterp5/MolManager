from __future__ import annotations

from molmanager.ui.theme import (
    THEME_DARK,
    THEME_LIGHT,
    current_theme_name,
    filter_card_stylesheet,
    load_saved_theme_name,
    save_theme_name,
)


def test_theme_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_theme_name(THEME_DARK)
    assert load_saved_theme_name() == THEME_DARK
    save_theme_name(THEME_LIGHT)
    assert load_saved_theme_name() == THEME_LIGHT


def test_filter_card_stylesheet_uses_palette_roles():
    qss = filter_card_stylesheet()
    assert "palette(base)" in qss.lower()
    assert "QFrame#FilterCard" in qss
    assert "QPushButton#fcToggle" in qss
    assert "QPushButton#fcToggle[fcActive=\"true\"]" in qss
    # Same rules for both themes — colors come from the application palette.
    assert filter_card_stylesheet(THEME_LIGHT) == filter_card_stylesheet(THEME_DARK)


def test_apply_application_theme_sets_current(qapp):
    from PyQt5.QtWidgets import QApplication

    from molmanager.ui.theme import apply_application_theme

    apply_application_theme(QApplication.instance(), THEME_DARK)
    assert current_theme_name() == THEME_DARK
    apply_application_theme(QApplication.instance(), THEME_LIGHT)
    assert current_theme_name() == THEME_LIGHT


def test_both_themes_use_fusion_without_global_stylesheet(qapp):
    from PyQt5.QtWidgets import QApplication

    from molmanager.ui.theme import apply_application_theme

    app = QApplication.instance()
    apply_application_theme(app, THEME_LIGHT)
    assert app.style().objectName().lower() == "fusion"
    assert app.styleSheet() == ""
    apply_application_theme(app, THEME_DARK)
    assert app.styleSheet() == ""
