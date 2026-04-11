"""
py2app setup script for Kindle Tablet macOS .app bundle.

Usage
-----
    # Install build deps first (once):
    pip install py2app

    # Build the app:
    cd /path/to/kindle-scribe-drawing-tablet
    python packaging/macos/build_app.py py2app

The resulting .app is placed in  dist/Kindle Tablet.app
"""
from setuptools import setup

APP = ["packaging/macos/launcher.py"]

DATA_FILES = []

OPTIONS = {
    "argv_emulation": False,   # keep False – argv emulation breaks Tk on macOS 13+
    "iconfile": "packaging/macos/AppIcon.icns",  # see packaging/macos/make_icon.py
    "plist": {
        "CFBundleName": "Kindle Tablet",
        "CFBundleDisplayName": "Kindle Tablet",
        "CFBundleIdentifier": "com.kindletablet.app",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "© 2025 kindle-scribe-drawing-tablet contributors",
        # Allow the app to communicate over the network (SSH)
        "com.apple.security.network.client": True,
    },
    "packages": [
        "kindle_tablet",
        "paramiko",
        "tkinter",
        "_tkinter",
    ],
    "includes": [
        "kindle_tablet.gui",
        "kindle_tablet.main",
        "kindle_tablet.connector",
        "kindle_tablet.config",
        "kindle_tablet.events",
        "kindle_tablet.input_macos",
    ],
    "excludes": [
        "kindle_tablet.input_windows",
        "test",
        "unittest",
    ],
    # Strip debug symbols for a smaller bundle
    "strip": True,
    # Build intermediates go here (keeps repo root clean)
    "bdist_base": "/tmp/py2app-build",
    "build_base": "/tmp/py2app-build",
}

setup(
    name="Kindle Tablet",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
