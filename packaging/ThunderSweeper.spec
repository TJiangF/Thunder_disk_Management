# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: builds the single-file ``ThunderSweeper`` CLI binary.

The bundled binary embeds a static ffmpeg (from imageio-ffmpeg), so end users
do not need Homebrew or any other install step.
"""

import os

from PyInstaller.utils.hooks import collect_data_files

PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = collect_data_files("imageio_ffmpeg")

hiddenimports = [
    "websocket",
    "imageio_ffmpeg",
    "thunder_sweeper.wizard",
    "thunder_sweeper.selftest",
]

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data", "test", "PyQt5", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ThunderSweeper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
