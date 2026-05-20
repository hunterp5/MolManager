"""Bundled resource and external tool path resolution."""

from __future__ import annotations

from pathlib import Path

from chemmanager import bundled_paths


def test_default_external_executable_falls_back_to_name():
    assert bundled_paths.default_external_executable("vina") in ("vina", "vina.exe")


def test_resolve_bundled_executable_when_present(tmp_path, monkeypatch):
    exe = tmp_path / "vina.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("CHEMMANAGER_BUNDLE_DIR", str(tmp_path))
    assert bundled_paths.resolve_bundled_executable("vina") == exe
    assert bundled_paths.default_external_executable("vina") == str(exe)


def test_static_asset_path_points_at_3dmol():
    p = bundled_paths.static_asset_path("3Dmol-min.js")
    assert p.name == "3Dmol-min.js"
    assert p.parent.name == "static"
    assert Path(bundled_paths.package_root(), "ui", "static", "3Dmol-min.js") == p
