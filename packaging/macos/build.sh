#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# packaging/macos/build.sh
# Builds "Kindle Tablet.app" for macOS using PyInstaller.
#
# Usage:
#   cd /path/to/kindle-scribe-drawing-tablet
#   bash packaging/macos/build.sh
#
# Output:  dist/Kindle Tablet.app
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

echo "==> Checking Python version…"
python3 --version

echo "==> Installing/upgrading build dependencies…"
pip install --quiet --upgrade pip
pip install --quiet "pyinstaller" "Pillow>=10" "pystray>=0.19"
pip install --quiet -e ".[all]"

echo "==> Generating .icns icon…"
python3 packaging/macos/make_icon.py

echo "==> Cleaning previous artefacts…"
rm -rf "dist/Kindle Tablet.app" "dist/Kindle Tablet"

echo "==> Running PyInstaller…"
pyinstaller packaging/macos/kindle_tablet.spec --noconfirm

echo ""
echo "✅  Done!  App bundle: dist/Kindle Tablet.app"
echo ""
echo "To create a distributable DMG (requires create-dmg):"
echo "  brew install create-dmg"
echo "  create-dmg \\"
echo "    --volname 'Kindle Tablet' \\"
echo "    --window-size 540 380 \\"
echo "    --icon 'Kindle Tablet.app' 160 190 \\"
echo "    --app-drop-link 380 190 \\"
echo "    'dist/Kindle Tablet.dmg' \\"
echo "    'dist/Kindle Tablet.app'"
