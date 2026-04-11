"""
Entry-point script for the macOS .app bundle.
py2app executes this file as __main__ when the user opens the .app.
"""
import sys
import os

# Ensure the bundled site-packages take priority
if getattr(sys, "frozen", False):
    # Running inside .app bundle — nothing special needed,
    # py2app already set sys.path correctly.
    pass

from kindle_tablet.gui import main

if __name__ == "__main__":
    main()
