# Kindle Scribe tablet

This is genuinely slop resulting of me screaming at multiple LLMs. I am sure it can be improved a lot. It is a buggy mess right now.
Any contributions are welcome.

Roadmap:
- [ ] fix buttons on tablet
- [ ] add support for eraser events

The way this works:
1. reads via ssh data from kindle's pen input
2. transforms that input into events

Tested on MacOS at the moment

How to use:
1. Launch ssh server in Koreader
2. `./deploy.sh kindle-ip`
3. `source .venv/bin/activate`
4. `kindle-tablet --host kindle-ip`

## Desktop GUI (macOS & Windows)

A graphical app is available so you don't have to use the command line.
It provides a window with Start / Stop controls, connection settings, and a live log.

### Run without building (any platform)

```bash
pip install -e ".[all]"   # installs dependencies + optional system-tray support
kindle-tablet-ui           # launch the GUI
```

Or directly:

```bash
python -m kindle_tablet.gui
```

### macOS — build a `.app` bundle

**Prerequisites:** Python 3.9+, Xcode command-line tools (`xcode-select --install`)

```bash
bash packaging/macos/build.sh
```

Output: `dist/Kindle Tablet.app` — double-click to launch, or drag to `/Applications`.

> **First launch blocked by Gatekeeper?**  Right-click → Open → Open, or run:
> ```bash
> xattr -dr com.apple.quarantine "dist/Kindle Tablet.app"
> ```

Optional — wrap in a DMG for distribution:

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

### Windows — build a `.exe`

**Prerequisites:** Python 3.9+ (from python.org), pip

```bat
packaging\windows\build.bat
```

Output: `dist\KindleTablet\KindleTablet.exe` — the whole folder must be kept together.
To make a portable ZIP:

```powershell
Compress-Archive dist\KindleTablet dist\KindleTablet.zip
```

> Requires [vmulti-bin](https://github.com/X9VoiD/vmulti-bin) for pen input injection.

## On windows (CLI):
Requires [vmulti-bin](https://github.com/X9VoiD/vmulti-bin) to be installed(gotta check if possible without it).

## Building notes (Kindle firmware):
```
make docker-image
docker build --target toolchain -t kindle-toolchain .
docker run --privileged --name sdk-builder -u builder kindle-toolchain /bin/sh -c "cd ~/kindle-sdk && ./gen-sdk.sh kindlehf"
docker commit sdk-builder kindle-sdk-final
```
