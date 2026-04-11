"""
Entry-point for the Windows .exe bundle (PyInstaller).
PyInstaller runs this as __main__ when the user launches KindleTablet.exe.
"""
import sys

# When frozen, stdout/stderr might be None (windowed app, no console).
# Redirect to a log file so errors are not silently swallowed.
if getattr(sys, "frozen", False):
    import os
    from pathlib import Path

    log_dir = Path.home() / ".config" / "kindle-tablet"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(log_dir / "app.log", "a", encoding="utf-8", buffering=1)

    if sys.stdout is None:
        sys.stdout = log_file
    if sys.stderr is None:
        sys.stderr = log_file

from kindle_tablet.gui import main

if __name__ == "__main__":
    main()
