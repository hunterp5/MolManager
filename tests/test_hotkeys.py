"""Hotkey registry and persistence."""

from __future__ import annotations

from molmanager.ui.hotkeys import (
    clear_hotkey_overrides,
    default_shortcuts,
    effective_shortcuts,
    find_duplicate_bindings,
    load_hotkey_overrides,
    save_hotkey_overrides,
)


def test_default_and_override_roundtrip():
    clear_hotkey_overrides()
    assert "Ctrl+F" in default_shortcuts("tools.search")
    save_hotkey_overrides({"tools.search": ["Ctrl+Shift+F"]})
    assert effective_shortcuts("tools.search") == ["Ctrl+Shift+F"]
    assert effective_shortcuts("file.open") == default_shortcuts("file.open")
    clear_hotkey_overrides()
    assert effective_shortcuts("tools.search") == default_shortcuts("tools.search")


def test_find_duplicate_bindings():
    dups = find_duplicate_bindings(
        {
            "a": ["Ctrl+F"],
            "b": ["Ctrl+F"],
            "c": [],
        }
    )
    assert "Ctrl+F" in dups
    assert set(dups["Ctrl+F"]) == {"a", "b"}
