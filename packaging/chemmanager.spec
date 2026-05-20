# -*- mode: python ; coding: utf-8 -*-
# Starter PyInstaller spec — run from repo root:
#   pyinstaller packaging/chemmanager.spec

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent.parent

block_cipher = None

a = Analysis(
    [str(ROOT / "chemmanager" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "chemmanager" / "ui" / "static"), "chemmanager/ui/static"),
        (str(ROOT / "chemmanager" / "resources" / "README.md"), "chemmanager/resources"),
    ],
    hiddenimports=[
        "rdkit",
        "rdkit.Chem",
        "rdkit.Chem.Draw",
        "rdkit.Chem.Draw.rdMolDraw2D",
        "PyQt5.QtWebEngineWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChemManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ChemManager",
)
