#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# packaging/macos/build.sh
# Builds "Kindle Tablet.app" for macOS using py2app.
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
pip install --quiet "py2app" "Pillow>=10" "pystray>=0.19"
pip install --quiet -e ".[all]"

echo "==> Generating .icns icon…"
python3 packaging/macos/make_icon.py

echo "==> Cleaning previous build artefacts…"
rm -rf packaging/macos/build packaging/macos/dist

echo "==> Running py2app…"
python3 packaging/macos/build_app.py py2app \
    --dist-dir dist \
    --build-dir packaging/macos/build

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
