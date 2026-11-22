# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for a single-file Windows (or local) GUI executable.
# Build with: pyinstaller --noconfirm --clean dflash_to_eee.spec
#
# The release workflow writes dflash_eee/build_info.py (RELEASE_TAG and
# COMMIT) after the tests and before this spec runs. A local build leaves
# those strings empty, and the app then says it is running from a build
# with no release tag.

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

# Message catalogs. PyInstaller unpacks datas next to the code in _MEIPASS,
# which is where dflash_eee.i18n looks when the exe is frozen.
try:
    _spec_root = os.path.dirname(os.path.abspath(SPEC))
except NameError:
    _spec_root = os.path.abspath(".")
_locales = os.path.join(_spec_root, "dflash_eee", "locales")
if not os.path.isdir(_locales):
    raise SystemExit("message catalogs not found: %s" % _locales)
datas.append((_locales, os.path.join("dflash_eee", "locales")))

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
