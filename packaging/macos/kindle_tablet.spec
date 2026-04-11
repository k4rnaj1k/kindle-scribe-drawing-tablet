# -*- mode: python ; coding: utf-8 -*-
# ---------------------------------------------------------------------------
# packaging/macos/kindle_tablet.spec
# PyInstaller spec for building "Kindle Tablet.app" on macOS.
#
# Usage (run from repo root):
#   pip install pyinstaller Pillow pystray
#   pyinstaller packaging/macos/kindle_tablet.spec
#
# Output: dist/Kindle Tablet.app
# ---------------------------------------------------------------------------
from pathlib import Path

REPO_ROOT = Path(SPECPATH).parent.parent   # two levels up from packaging/macos/

block_cipher = None

a = Analysis(
    [str(REPO_ROOT / "packaging" / "macos" / "launcher.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "paramiko",
        "paramiko.transport",
        "Crypto",
        "Crypto.Cipher",
        "Crypto.PublicKey",
        "Crypto.Hash",
        "bcrypt",
        # macOS input backend
        "kindle_tablet.input_macos",
        # System-tray
        "pystray",
        "pystray._darwin",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageTk",
        # AppKit needed for Dock activation
        "AppKit",
        "Foundation",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "kindle_tablet.input_windows",
        "unittest",
    ],
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
    name="Kindle Tablet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can break macOS code-signing
    console=False,      # windowed – no terminal window
    disable_windowed_traceback=False,
    target_arch=None,   # build for current arch (arm64 on M-series runners)
    codesign_identity=None,
    entitlements_file=None,
    icon=str(REPO_ROOT / "packaging" / "macos" / "AppIcon.icns"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Kindle Tablet",
)

# ── Wrap the COLLECT output in a .app bundle ─────────────────────────────────
app = BUNDLE(
    coll,
    name="Kindle Tablet.app",
    icon=str(REPO_ROOT / "packaging" / "macos" / "AppIcon.icns"),
    bundle_identifier="com.kindletablet.app",
    info_plist={
        "CFBundleName": "Kindle Tablet",
        "CFBundleDisplayName": "Kindle Tablet",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "\u00a9 2025 kindle-scribe-drawing-tablet contributors",
        "com.apple.security.network.client": True,
    },
)
