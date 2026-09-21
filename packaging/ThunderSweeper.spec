# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: builds ``dist/ThunderSweeper/`` (a *onedir* bundle).

onedir is used on purpose: a onefile build must unpack the 47 MB embedded
ffmpeg to a temp folder on every launch (~10 s startup).  A folder starts
instantly, and end users still only see a single folder + .app.
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
    "thunder_sweeper.local_disk",
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
    [],
    exclude_binaries=True,
    name="ThunderSweeper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ThunderSweeper",
)
