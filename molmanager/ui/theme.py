"""Application GUI themes (Fusion style + palette only)."""

from __future__ import annotations

import colorsys
import json
import random

from PyQt5.QtCore import QSettings
from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import QApplication, QWidget

THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_GROOVY = "groovy"
THEME_CUSTOM = "custom"

_SETTINGS_ORG = "MolManager"
_SETTINGS_APP = "MolManager"
_SETTINGS_KEY_THEME = "gui/theme"
_SETTINGS_KEY_CUSTOM_PALETTE = "gui/custom_palette"
_SETTINGS_KEY_TABLE_FONT_PT = "gui/table_font_pt"
_SETTINGS_KEY_APP_FONT_PT = "gui/app_font_pt"

_CURRENT_THEME = THEME_LIGHT

_FC_CTRL_H = 20

MIN_FONT_PT = 8
MAX_FONT_PT = 32
DEFAULT_FONT_PT = 10
# Backwards-compatible aliases (table-specific names used elsewhere).
MIN_TABLE_FONT_PT = MIN_FONT_PT
MAX_TABLE_FONT_PT = MAX_FONT_PT

# User-editable roles for the Custom theme (key → label, QPalette role).
CUSTOM_PALETTE_ROLES: tuple[tuple[str, str, object], ...] = (
    ("window", "Window", QPalette.Window),
    ("window_text", "Window text", QPalette.WindowText),
    ("base", "Base (tables / fields)", QPalette.Base),
    ("alternate_base", "Alternate base", QPalette.AlternateBase),
    ("text", "Text", QPalette.Text),
    ("button", "Button", QPalette.Button),
    ("button_text", "Button text", QPalette.ButtonText),
    ("highlight", "Highlight (selection)", QPalette.Highlight),
    ("highlighted_text", "Highlighted text", QPalette.HighlightedText),
    ("mid", "Mid (borders)", QPalette.Mid),
    ("light", "Light", QPalette.Light),
    ("dark", "Dark", QPalette.Dark),
    ("link", "Link", QPalette.Link),
    ("tooltip_base", "Tooltip background", QPalette.ToolTipBase),
    ("tooltip_text", "Tooltip text", QPalette.ToolTipText),
)


def current_theme_name() -> str:
    return _CURRENT_THEME


def _clamp_font_pt(pt: int) -> int:
    return max(MIN_FONT_PT, min(MAX_FONT_PT, int(pt)))


def default_app_font_pt() -> int:
    """Default application-wide font point size."""
    return DEFAULT_FONT_PT


def default_table_font_pt() -> int:
    """Default table font size (matches the application default)."""
    return DEFAULT_FONT_PT


def _load_saved_font_pt(key: str) -> int:
    raw = QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(key, 0)
    try:
        pt = int(raw)
    except (TypeError, ValueError):
        pt = 0
    if pt <= 0:
        return default_app_font_pt()
    return _clamp_font_pt(pt)


def load_saved_table_font_pt() -> int:
    """Saved table font point size, clamped to the supported range (default when unset)."""
    return _load_saved_font_pt(_SETTINGS_KEY_TABLE_FONT_PT)


def save_table_font_pt(pt: int) -> None:
    QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_SETTINGS_KEY_TABLE_FONT_PT, int(pt))


def load_saved_app_font_pt() -> int:
    """Saved application-wide font point size, clamped (default when unset)."""
    return _load_saved_font_pt(_SETTINGS_KEY_APP_FONT_PT)


def save_app_font_pt(pt: int) -> None:
    QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_SETTINGS_KEY_APP_FONT_PT, int(pt))


def apply_application_font_pt(pt: int) -> int:
    """Set the application-wide font point size; returns the clamped size applied."""
    app = QApplication.instance()
    pt = _clamp_font_pt(pt)
    if app is not None:
        font = QFont(app.font())
        font.setPointSize(pt)
        app.setFont(font)
    return pt


def _normalize_theme_name(theme: str | None) -> str:
    raw = str(theme or "").strip().lower().replace("-", "_")
    if raw in ("dark", "dark_mode"):
        return THEME_DARK
    if raw in ("groovy", "groovy_mode", "psychedelic"):
        return THEME_GROOVY
    if raw in ("custom", "custom_mode"):
        return THEME_CUSTOM
    return THEME_LIGHT


def load_saved_theme_name() -> str:
    """Return the user's saved GUI theme, or light when none has been chosen yet."""
    settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    if not settings.contains(_SETTINGS_KEY_THEME):
        return THEME_LIGHT
    return _normalize_theme_name(str(settings.value(_SETTINGS_KEY_THEME) or ""))


def save_theme_name(theme: str) -> None:
    name = _normalize_theme_name(theme)
    QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_SETTINGS_KEY_THEME, name)


def default_custom_palette_colors() -> dict[str, str]:
    """Default Custom theme colors seeded from the Fusion light palette."""
    p = _light_palette()
    out: dict[str, str] = {}
    for key, _label, role in CUSTOM_PALETTE_ROLES:
        out[key] = QColor(p.color(role)).name()
    return out


def load_saved_custom_palette_colors() -> dict[str, str]:
    """Saved Custom theme colors merged onto the light defaults."""
    base = default_custom_palette_colors()
    settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    raw = settings.value(_SETTINGS_KEY_CUSTOM_PALETTE, "")
    data: dict | None = None
    if isinstance(raw, dict):
        data = raw
    else:
        text = str(raw or "").strip()
        if text:
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
    if not data:
        return base
    for key, _label, _role in CUSTOM_PALETTE_ROLES:
        val = data.get(key)
        if not isinstance(val, str):
            continue
        c = QColor(val)
        if c.isValid():
            base[key] = c.name()
    return base


def save_custom_palette_colors(colors: dict[str, str]) -> dict[str, str]:
    """Persist Custom theme colors; returns the normalized saved mapping."""
    base = default_custom_palette_colors()
    for key, _label, _role in CUSTOM_PALETTE_ROLES:
        val = colors.get(key)
        if not isinstance(val, str):
            continue
        c = QColor(val)
        if c.isValid():
            base[key] = c.name()
    QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(
        _SETTINGS_KEY_CUSTOM_PALETTE,
        json.dumps(base, separators=(",", ":"), sort_keys=True),
    )
    return base


def _custom_palette(colors: dict[str, str] | None = None) -> QPalette:
    """Build a Fusion palette from saved/edited Custom theme colors."""
    merged = default_custom_palette_colors()
    if colors:
        for key, val in colors.items():
            if key not in merged or not isinstance(val, str):
                continue
            c = QColor(val)
            if c.isValid():
                merged[key] = c.name()
    p = QPalette()
    for key, _label, role in CUSTOM_PALETTE_ROLES:
        p.setColor(role, QColor(merged[key]))
    # Derived extras for readability when disabled.
    mid = QColor(merged["mid"])
    disabled = QColor(mid.red(), mid.green(), mid.blue(), 160)
    p.setColor(QPalette.BrightText, QColor(255, 255, 80))
    p.setColor(QPalette.Shadow, QColor(20, 20, 20))
    p.setColor(QPalette.Disabled, QPalette.Text, disabled)
    p.setColor(QPalette.Disabled, QPalette.ButtonText, disabled)
    p.setColor(QPalette.Disabled, QPalette.WindowText, disabled)
    return p


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
    Compact filter cards — palette-driven to match Fusion light/dark app chrome.
    """
    del theme
    h = _FC_CTRL_H
    return f"""
    QFrame#FilterCard {{
        background-color: palette(base);
        border: 1px solid palette(mid);
        border-radius: 6px;
    }}
    QFrame#FilterCard QLabel {{
        font-size: 11px;
        color: palette(windowtext);
        background: transparent;
    }}
    QFrame#FilterCard QLabel#fcMiniLabel {{
        font-size: 10px;
        color: palette(mid);
        min-width: 26px;
        max-width: 26px;
    }}
    QFrame#FilterCard QLabel#fcSectionTitle {{
        font-size: 11px;
        font-weight: 600;
        color: palette(windowtext);
    }}
    QFrame#FilterCard QComboBox,
    QFrame#FilterCard QLineEdit {{
        min-height: {h}px;
        max-height: {h}px;
        font-size: 11px;
        border: 1px solid palette(mid);
        border-radius: 4px;
        padding: 0px 6px;
        background-color: palette(base);
        color: palette(text);
        selection-background-color: palette(highlight);
        selection-color: palette(highlightedtext);
    }}
    QFrame#FilterCard QComboBox:focus,
    QFrame#FilterCard QLineEdit:focus {{
        border-color: palette(highlight);
    }}
    QFrame#FilterCard QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 16px;
        border: none;
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
        padding: 0px 7px;
        font-size: 10px;
        min-height: {h}px;
        max-height: {h}px;
        min-width: 44px;
        border: 1px solid palette(mid);
        border-radius: 4px;
        background-color: palette(button);
        color: palette(buttontext);
    }}
    QFrame#FilterCard QPushButton#fcToggle:hover {{
        background-color: palette(light);
    }}
    QFrame#FilterCard QPushButton#fcToggle[fcActive="true"] {{
        border-color: palette(highlight);
        background-color: palette(highlight);
        color: palette(highlightedtext);
        font-weight: 600;
    }}
    QFrame#FilterCard QPushButton#fcRemove {{
        min-width: 18px;
        max-width: 18px;
        min-height: 18px;
        max-height: 18px;
        color: palette(mid);
        background-color: transparent;
        border: none;
        border-radius: 4px;
        font-size: 15px;
        font-weight: bold;
        padding: 0px;
    }}
    QFrame#FilterCard QPushButton#fcRemove:hover {{
        color: palette(link);
        background-color: palette(alternatebase);
    }}
    QFrame#FilterCard QSlider::groove:horizontal {{
        height: 3px;
        background: palette(mid);
        border-radius: 2px;
    }}
    QFrame#FilterCard QSlider::handle:horizontal {{
        width: 10px;
        height: 10px;
        margin: -4px 0;
        background: palette(highlight);
        border: 1px solid palette(dark);
        border-radius: 5px;
    }}
    QFrame#FilterCard QSlider::handle:horizontal:hover {{
        background: palette(light);
    }}
    QFrame#FilterCard QListWidget {{
        font-size: 10px;
        border: 1px solid palette(mid);
        border-radius: 4px;
        background-color: palette(base);
        color: palette(text);
        outline: 0;
    }}
    QFrame#FilterCard QListWidget::item {{
        padding: 1px 4px;
        min-height: 14px;
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


def _hsv_qcolor(h: float, s: float, v: float) -> QColor:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return QColor(int(r * 255), int(g * 255), int(b * 255))


def _contrasting_text(bg: QColor) -> QColor:
    # Relative luminance (sRGB-ish) → black or white for readable labels.
    lum = (0.2126 * bg.red() + 0.7152 * bg.green() + 0.0722 * bg.blue()) / 255.0
    return QColor(18, 18, 22) if lum > 0.55 else QColor(250, 248, 255)


def _groovy_palette(rng: random.Random | None = None) -> QPalette:
    """Random high-saturation psychedelic Fusion palette (readable text contrast)."""
    rng = rng or random.Random()
    base_h = rng.random()

    def shift(delta: float, s: float, v: float) -> QColor:
        return _hsv_qcolor(base_h + delta, s, v)

    window = shift(0.00, rng.uniform(0.55, 0.95), rng.uniform(0.35, 0.75))
    base = shift(0.12, rng.uniform(0.45, 0.90), rng.uniform(0.22, 0.55))
    alt = shift(0.22, rng.uniform(0.50, 0.95), rng.uniform(0.30, 0.65))
    button = shift(0.35, rng.uniform(0.60, 1.0), rng.uniform(0.40, 0.85))
    highlight = shift(0.55, rng.uniform(0.75, 1.0), rng.uniform(0.55, 0.95))
    link = shift(0.70, rng.uniform(0.70, 1.0), rng.uniform(0.55, 0.95))
    mid = shift(0.08, rng.uniform(0.25, 0.55), rng.uniform(0.35, 0.60))
    light = shift(0.05, rng.uniform(0.35, 0.70), rng.uniform(0.70, 0.95))
    dark = shift(0.02, rng.uniform(0.40, 0.80), rng.uniform(0.12, 0.30))
    tip = shift(0.40, rng.uniform(0.50, 0.90), rng.uniform(0.35, 0.70))

    text = _contrasting_text(base)
    window_text = _contrasting_text(window)
    button_text = _contrasting_text(button)
    hi_text = _contrasting_text(highlight)
    tip_text = _contrasting_text(tip)
    disabled = QColor(mid.red(), mid.green(), mid.blue(), 160)

    p = QPalette()
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, window_text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.ToolTipBase, tip)
    p.setColor(QPalette.ToolTipText, tip_text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, button)
    p.setColor(QPalette.ButtonText, button_text)
    p.setColor(QPalette.BrightText, QColor(255, 255, 80))
    p.setColor(QPalette.Link, link)
    p.setColor(QPalette.Highlight, highlight)
    p.setColor(QPalette.HighlightedText, hi_text)
    p.setColor(QPalette.Mid, mid)
    p.setColor(QPalette.Dark, dark)
    p.setColor(QPalette.Light, light)
    p.setColor(QPalette.Shadow, QColor(10, 5, 20))
    p.setColor(QPalette.Disabled, QPalette.Text, disabled)
    p.setColor(QPalette.Disabled, QPalette.ButtonText, disabled)
    p.setColor(QPalette.Disabled, QPalette.WindowText, disabled)
    return p


def palette_for_theme(theme: str, *, rng: random.Random | None = None) -> QPalette:
    """Return a QPalette for *theme* (groovy is freshly randomized each call)."""
    name = _normalize_theme_name(theme)
    if name == THEME_DARK:
        return _dark_palette()
    if name == THEME_GROOVY:
        return _groovy_palette(rng)
    if name == THEME_CUSTOM:
        return _custom_palette(load_saved_custom_palette_colors())
    return _light_palette()


def refresh_open_windows_theme(app: QApplication | None = None) -> None:
    """
    Push the current application palette to open top-level windows and invoke
    optional ``refresh_theme()`` hooks (sketcher, tool dialogs, etc.).
    """
    if app is None:
        app = QApplication.instance()
    if app is None:
        return
    pal = app.palette()
    for w in app.topLevelWidgets():
        try:
            if w is None:
                continue
            w.setPalette(pal)
            refresh = getattr(w, "refresh_theme", None)
            if callable(refresh):
                refresh()
            style = w.style()
            if style is not None:
                style.unpolish(w)
                style.polish(w)
            w.update()
        except RuntimeError:
            # Widget deleted between listing and update.
            continue


def apply_application_theme(app: QApplication | None, theme: str) -> str:
    """Apply *theme* to *app*; returns the theme name actually applied."""
    global _CURRENT_THEME
    if app is None:
        return _normalize_theme_name(theme)
    theme = _normalize_theme_name(theme)
    _CURRENT_THEME = theme
    app.setStyle("Fusion")
    app.setPalette(palette_for_theme(theme))
    # No global stylesheet — Fusion draws from the palette so modes share chrome layout.
    app.setStyleSheet("")
    refresh_open_windows_theme(app)
    return theme
