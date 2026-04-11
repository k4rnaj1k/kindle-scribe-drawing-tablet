# Building the Kindle Tablet desktop application

The GUI lives in `kindle_tablet/gui.py` and is built on Python's built-in
**tkinter**. No extra runtime dependencies are required to run the GUI itself;
system-tray support is optional and enabled automatically when `pystray` and
`Pillow` are installed.

---

## CI builds (GitHub Actions)

Every push to any branch automatically builds both platform apps via
`.github/workflows/build_gui.yml`. The resulting artefacts are available on the
**Actions** tab for 30 days:

| Artefact | Platform |
|---|---|
| `KindleTablet-macOS` | `Kindle Tablet.app` (zipped) |
| `KindleTablet-Windows` | `KindleTablet/` folder (zipped) |

---

## Running without building (any platform)

```bash
pip install -e ".[all]"   # installs kindle-tablet-ui entry-point + tray deps
kindle-tablet-ui           # launch the GUI
```

Or directly:

```bash
python -m kindle_tablet.gui
```

---

## macOS — build a `.app` bundle locally

**Prerequisites:** Python 3.9+, Xcode command-line tools (`xcode-select --install`)

```bash
# From the repo root:
bash packaging/macos/build.sh
```

This will:
1. Install `py2app`, `Pillow`, `pystray`, and the package itself
2. Generate `packaging/macos/AppIcon.icns` via `make_icon.py`
3. Run `py2app` to produce **`dist/Kindle Tablet.app`**

Double-click `dist/Kindle Tablet.app` in Finder to launch, or copy it to
`/Applications`.

### Optional — wrap in a DMG

```bash
brew install create-dmg
create-dmg \
  --volname "Kindle Tablet" \
  --window-size 540 380 \
  --icon "Kindle Tablet.app" 160 190 \
  --app-drop-link 380 190 \
  "dist/Kindle Tablet.dmg" \
  "dist/Kindle Tablet.app"
```

### macOS Gatekeeper (first launch)

Because the app is not notarised, macOS will block it on first open. Either:

- Right-click → Open → Open (one-time bypass), **or**
- `xattr -dr com.apple.quarantine "dist/Kindle Tablet.app"`

---

## Windows — build a `.exe` locally

**Prerequisites:** Python 3.9+ (from python.org, not the Store), pip

```bat
REM From the repo root:
packaging\windows\build.bat
```

This will:
1. Install `PyInstaller`, `Pillow`, `pystray`, and the package itself
2. Generate `packaging\windows\app.ico`
3. Run `PyInstaller` to produce **`dist\KindleTablet\KindleTablet.exe`**

The entire `dist\KindleTablet\` folder must be kept together. To distribute,
zip it:

```powershell
Compress-Archive dist\KindleTablet dist\KindleTablet.zip
```

### Antivirus false-positives

PyInstaller bundles are sometimes flagged. If needed, add the `dist\KindleTablet`
folder to your AV exclusion list, or sign the executable with a code-signing
certificate.

---

## File layout

```
packaging/
├── macos/
│   ├── build.sh          # one-command macOS build script
│   ├── build_app.py      # py2app setup() script
│   ├── launcher.py       # .app entry-point
│   └── make_icon.py      # generates AppIcon.icns
└── windows/
    ├── build.bat         # one-command Windows build script
    ├── kindle_tablet.spec # PyInstaller spec
    ├── launcher.py       # .exe entry-point
    ├── make_icon.py      # generates app.ico
    └── version_info.txt  # Windows VERSIONINFO resource
```
