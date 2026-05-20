"""Application light/dark themes (Fusion style + palette only)."""

from __future__ import annotations

from PyQt5.QtCore import QSettings
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication, QWidget

THEME_LIGHT = "light"
THEME_DARK = "dark"

_SETTINGS_ORG = "MolManager"
_SETTINGS_APP = "MolManager"
_SETTINGS_KEY_THEME = "gui/theme"

_CURRENT_THEME = THEME_LIGHT

_FC_CTRL_H = 22


def current_theme_name() -> str:
    return _CURRENT_THEME


def load_saved_theme_name() -> str:
    raw = QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(_SETTINGS_KEY_THEME, THEME_LIGHT)
    return THEME_DARK if str(raw or "").strip().lower() in ("dark", "dark_mode") else THEME_LIGHT


def save_theme_name(theme: str) -> None:
    QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(
        _SETTINGS_KEY_THEME,
        THEME_DARK if theme == THEME_DARK else THEME_LIGHT,
    )


def polish_widget_property(widget: QWidget, prop: str, value: object) -> None:
    """Apply a dynamic Qt style property (e.g. ``fcActive``) and re-polish."""
    widget.setProperty(prop, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def filter_panel_stylesheet() -> str:
    """Filter side panel chrome — palette-driven (light/dark follow app palette)."""
    return """
    QFrame#FilterPanel {
        background-color: palette(window);
        border-left: 1px solid palette(mid);
    }
    """


def filter_card_stylesheet(theme: str | None = None) -> str:
    """
    Stylesheet for filter panel cards (``QFrame#FilterCard``).

    Uses ``palette(...)`` roles so light mode matches Fusion defaults and dark mode
    mirrors the same structure via :func:`_dark_palette` (not separate hex rules).
    The *theme* argument is accepted for API compatibility but ignored.
    """
    del theme
    h = _FC_CTRL_H
    return f"""
    QFrame#FilterCard {{
        background-color: palette(alternatebase);
        border: 1px solid palette(mid);
        border-radius: 4px;
    }}
    QFrame#FilterCard QLabel,
    QFrame#FilterCard QLabel#fcSectionTitle {{
        font-size: 11px;
        color: palette(windowtext);
        background: transparent;
    }}
    QFrame#FilterCard QLabel#fcSectionTitle {{
        font-weight: 600;
    }}
    QFrame#FilterCard QComboBox,
    QFrame#FilterCard QLineEdit {{
        min-height: {h}px;
        max-height: {h}px;
        font-size: 11px;
        border: 1px solid palette(mid);
        border-radius: 3px;
        padding: 1px 6px;
        background-color: palette(base);
        color: palette(text);
        selection-background-color: palette(highlight);
        selection-color: palette(highlightedtext);
    }}
    QFrame#FilterCard QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 18px;
        border-left: 1px solid palette(mid);
    }}
    QFrame#FilterCard QComboBox QAbstractItemView {{
        background-color: palette(base);
        color: palette(text);
        border: 1px solid palette(mid);
        selection-background-color: palette(highlight);
        selection-color: palette(highlightedtext);
    }}
    QFrame#FilterCard QPushButton#fcToggle {{
        padding: 2px 8px;
        font-size: 11px;
        min-height: {h}px;
        max-height: {h}px;
        border: 1px solid palette(mid);
        border-radius: 3px;
        background-color: palette(button);
        color: palette(buttontext);
    }}
    QFrame#FilterCard QPushButton#fcToggle:hover {{
        background-color: palette(light);
        border-color: palette(dark);
    }}
    QFrame#FilterCard QPushButton#fcToggle[fcActive="true"] {{
        border: 1px solid palette(highlight);
        background-color: palette(highlight);
        color: palette(highlightedtext);
        font-weight: 600;
    }}
    QFrame#FilterCard QPushButton#fcRefresh {{
        padding: 2px 8px;
        font-size: 11px;
        min-height: {h}px;
        max-height: {h}px;
        border: 1px solid palette(mid);
        border-radius: 3px;
        background-color: palette(button);
        color: palette(buttontext);
    }}
    QFrame#FilterCard QPushButton#fcRefresh:hover {{
        background-color: palette(light);
        border-color: palette(dark);
    }}
    QFrame#FilterCard QPushButton#fcRemove {{
        min-width: 22px;
        max-width: 22px;
        min-height: 22px;
        max-height: 22px;
        color: palette(link);
        background-color: palette(base);
        border: 1px solid palette(mid);
        border-radius: 3px;
        font-size: 14px;
        font-weight: bold;
        padding: 0px;
    }}
    QFrame#FilterCard QPushButton#fcRemove:hover {{
        background-color: palette(alternatebase);
        border-color: palette(dark);
    }}
    QFrame#FilterCard QSlider::groove:horizontal {{
        height: 4px;
        background: palette(mid);
        border-radius: 2px;
    }}
    QFrame#FilterCard QSlider::handle:horizontal {{
        width: 11px;
        height: 11px;
        margin: -5px 0;
        background: palette(highlight);
        border: 1px solid palette(dark);
        border-radius: 5px;
    }}
    QFrame#FilterCard QSlider::handle:horizontal:hover {{
        background: palette(light);
    }}
    QFrame#FilterCard QListWidget {{
        font-size: 11px;
        border: 1px solid palette(mid);
        border-radius: 3px;
        background-color: palette(base);
        color: palette(text);
        outline: 0;
    }}
    QFrame#FilterCard QListWidget::item {{
        padding: 2px 4px;
    }}
    QFrame#FilterCard QListWidget::item:hover {{
        background-color: palette(alternatebase);
    }}
    """


def _light_palette() -> QPalette:
    """Fusion default palette (light mode)."""
    return QApplication.style().standardPalette()


def _dark_palette() -> QPalette:
    """
    Dark palette with the same role layout as Fusion light.

    Accent roles (highlight, links) are copied from the light palette so selection
  and focus colors match; only surfaces and text are darkened.
    """
    light = _light_palette()
    text = QColor(240, 240, 240)
    disabled = QColor(128, 128, 128)
    p = QPalette()
    p.setColor(QPalette.Window, QColor(53, 53, 53))
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, QColor(35, 35, 35))
    p.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
    p.setColor(QPalette.ToolTipBase, QColor(53, 53, 53))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor(68, 68, 68))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, light.color(QPalette.BrightText))
    p.setColor(QPalette.Link, light.color(QPalette.Link))
    p.setColor(QPalette.Highlight, light.color(QPalette.Highlight))
    p.setColor(QPalette.HighlightedText, light.color(QPalette.HighlightedText))
    p.setColor(QPalette.Mid, QColor(128, 128, 128))
    p.setColor(QPalette.Dark, QColor(30, 30, 30))
    p.setColor(QPalette.Light, QColor(75, 75, 75))
    p.setColor(QPalette.Shadow, QColor(15, 15, 15))
    p.setColor(QPalette.Disabled, QPalette.Text, disabled)
    p.setColor(QPalette.Disabled, QPalette.ButtonText, disabled)
    return p


def apply_application_theme(app: QApplication | None, theme: str) -> str:
    """Apply *theme* to *app*; returns the theme name actually applied."""
    global _CURRENT_THEME
    if app is None:
        return theme
    theme = THEME_DARK if theme == THEME_DARK else THEME_LIGHT
    _CURRENT_THEME = theme
    app.setStyle("Fusion")
    app.setPalette(_dark_palette() if theme == THEME_DARK else _light_palette())
    # No global stylesheet in either mode — Fusion draws all widgets from the palette,
    # so light and dark share the same layout, borders, and table chrome.
    app.setStyleSheet("")
    return theme
