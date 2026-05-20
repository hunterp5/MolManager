"""Resolve bundled resources and optional external tool executables."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent

# Basenames searched under ``resources/bin/<platform>/`` (first match wins).
_TOOL_BINARIES: dict[str, tuple[str, ...]] = {
    "vina": ("vina.exe", "vina"),
    "boltz": ("boltz.exe", "boltz"),
}


def package_root() -> Path:
    return _PACKAGE_ROOT


def resources_dir() -> Path:
    return _PACKAGE_ROOT / "resources"


def _platform_bin_subdir() -> str:
    if sys.platform.startswith("win"):
        return "win"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def bundled_bin_dir() -> Path:
    override = (os.environ.get("MOLMANAGER_BUNDLE_DIR") or "").strip()
    if override:
        return Path(override)
    return resources_dir() / "bin" / _platform_bin_subdir()


def resolve_bundled_executable(tool: str) -> Path | None:
    """Return an executable path when shipped under ``resources/bin/<platform>/``."""
    key = (tool or "").strip().lower()
    names = _TOOL_BINARIES.get(key)
    if not names:
        return None
    base = bundled_bin_dir()
    if not base.is_dir():
        return None
    for name in names:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def default_external_executable(tool: str) -> str:
    """
    Prefer a bundled binary; otherwise return the bare tool name for PATH lookup.

    Set ``MOLMANAGER_BUNDLE_DIR`` to point at a directory containing platform binaries.
    """
    bundled = resolve_bundled_executable(tool)
    if bundled is not None:
        return str(bundled)
    key = (tool or "").strip().lower()
    if key in _TOOL_BINARIES:
        return _TOOL_BINARIES[key][-1]
    return tool


def static_asset_path(name: str) -> Path:
    """Path to a file under ``molmanager/ui/static`` (e.g. ``3Dmol-min.js``)."""
    return _PACKAGE_ROOT / "ui" / "static" / name
