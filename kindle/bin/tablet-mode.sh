#!/bin/sh
# tablet-mode.sh - Kindle Tablet Mode
#
# Disables Pillow and screensaver, launches the tablet-ui GTK app
# which shows an "Exit Tablet Mode" button. Awesome WM stays running
# so the GTK window can be managed normally.
#
# On exit, restores Pillow and screensaver.
#
# Usage: tablet-mode.sh [start|stop|status]

ACTION="${1:-start}"
MARKER="/tmp/tablet-mode-active"
EXT_DIR="/mnt/us/extensions/kindle-tablet"

# ---- Framework control ----

freeze_framework() {
    # Disable Pillow (status bar overlay)
    lipc-set-prop com.lab126.pillow disableEnablePillow disable 2>/dev/null

    # Prevent screensaver from blanking the display
    lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null
}

thaw_framework() {
    # Re-enable Pillow
    lipc-set-prop com.lab126.pillow disableEnablePillow enable 2>/dev/null

    # Allow screensaver again
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null
}

# ---- Start ----

start_tablet_mode() {
    if [ -f "$MARKER" ]; then
        echo "Tablet mode already active"; return
    fi

    echo "Starting tablet mode"

    # Write marker before anything else so stop works even if we crash
    echo "active" > "$MARKER"

    freeze_framework

    # Launch the GTK UI app (shows exit button)
    "$EXT_DIR/bin/tablet-ui" --marker-file "$MARKER" &
    GTK_PID=$!

    echo "Tablet mode running (GTK UI pid=$GTK_PID)"

    # Wait until the marker is removed.
    # tablet-ui removes it when exit button is tapped.
    # 'stop' action also removes it.
    while [ -f "$MARKER" ]; do sleep 1; done

    # Clean up GTK app if still running
    kill "$GTK_PID" 2>/dev/null

    do_stop
}

# ---- Stop ----

do_stop() {
    rm -f "$MARKER"
    rm -f /tmp/tablet-rotation

    # Kill UI if still running
    pkill -f "tablet-ui" 2>/dev/null

    # Restore screen rotation to portrait
    xrandr -o normal 2>/dev/null

    # Restore framework
    thaw_framework

    # Poke the framework to redraw
    lipc-send-event com.lab126.hal.usbError cycled 2>/dev/null

    echo "Tablet mode stopped."
}

stop_tablet_mode() {
    [ ! -f "$MARKER" ] && echo "Tablet mode not active" && return
    do_stop
}

# ---- Main ----

case "$ACTION" in
    start)  start_tablet_mode ;;
    stop)   stop_tablet_mode  ;;
    status)
        if [ -f "$MARKER" ]; then echo "active"
        else echo "inactive"; fi ;;
    *) echo "Usage: $0 [start|stop|status]"; exit 1 ;;
esac
