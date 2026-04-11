# -*- mode: python ; coding: utf-8 -*-
# ---------------------------------------------------------------------------
# packaging/windows/kindle_tablet.spec
# PyInstaller spec for building KindleTablet.exe on Windows.
#
# Usage (run from repo root):
#   pip install pyinstaller Pillow pystray
#   pyinstaller packaging/windows/kindle_tablet.spec
#
# Output: dist\KindleTablet\KindleTablet.exe  (folder bundle)
#         dist\KindleTablet.exe               (single-file, see onefile option below)
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

REPO_ROOT = Path(SPECPATH).parent.parent  # two levels up from packaging/windows/

block_cipher = None

a = Analysis(
    [str(REPO_ROOT / "packaging" / "windows" / "launcher.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Paramiko crypto backends
        "paramiko",
        "paramiko.transport",
        "Crypto",
        "Crypto.Cipher",
        "Crypto.PublicKey",
        "Crypto.Hash",
        "bcrypt",
        # Windows input backend
        "kindle_tablet.input_windows",
        # System-tray (optional but include if installed)
        "pystray",
        "pystray._win32",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageTk",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "kindle_tablet.input_macos",
        "AppKit",
        "Quartz",
        "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Single-folder EXE (fast startup, easier to debug) ─────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KindleTablet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # no console window – pure GUI app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(REPO_ROOT / "packaging" / "windows" / "app.ico"),
    version=str(REPO_ROOT / "packaging" / "windows" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="KindleTablet",
)
