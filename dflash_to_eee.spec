# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for a single-file Windows (or local) GUI executable.
# Build with: pyinstaller --noconfirm --clean dflash_to_eee.spec

import os

datas = []
binaries = []
hiddenimports = []

try:
    from PyInstaller.utils.hooks import collect_all
    dnd_datas, dnd_binaries, dnd_hidden = collect_all("tkinterdnd2")
    datas += dnd_datas
    binaries += dnd_binaries
    hiddenimports += dnd_hidden
except Exception:
    # Browse still works when tkinterdnd2 is not installed.
    pass

block_cipher = None

a = Analysis(
    ["gui.py"],
    pathex=[os.path.abspath(".")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="dflash_to_eee",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
