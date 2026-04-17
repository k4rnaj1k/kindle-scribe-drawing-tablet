#!/bin/sh
# tablet-mode.sh - Kindle Tablet Mode
#
# Disables Pillow and screensaver, then launches tablet-ui which handles
# both the GTK interface and TCP event streaming in a single process.
# (TCP streaming was formerly a separate tablet-daemon binary.)
# Awesome WM stays running so the GTK window can be managed normally.
#
# On exit, restores Pillow and screensaver.
#
# Usage: tablet-mode.sh [start|stop|status]

ACTION="${1:-start}"
MARKER="/tmp/tablet-mode-active"
EXT_DIR="/mnt/us/extensions/kindle-tablet"
TCP_PORT=8234

# ---- Pen device detection ----
#
# Mirrors the host's auto_detect_pen_device() (lexicographic shell glob over
# /sys/class/input/event*) so tablet-ui streams from the same evdev node the
# host will read caps from.  Without this, tablet-ui's internal readdir()
# auto-detect can pick a different node (e.g. ntx_event sensor instead of
# wacom_i2c digitizer) → coordinates stream with the wrong axis ranges and
# every event lands in the bottom-left corner of the host screen.
detect_pen_device() {
    for dev in /sys/class/input/event*/device; do
        [ -e "$dev/name" ] || continue
        evdev=$(basename $(dirname "$dev"))
        name=$(cat "$dev/name" 2>/dev/null | tr 'A-Z' 'a-z')
        for kw in wacom stylus pen digitizer ntx_event; do
            case "$name" in
                *"$kw"*) echo "/dev/input/$evdev"; return 0 ;;
            esac
        done
    done
    return 1
}

# ---- Framework control ----

freeze_framework() {
    # Disable Pillow (status bar overlay)
    lipc-set-prop com.lab126.pillow disableEnablePillow disable 2>/dev/null

    # Prevent screensaver from blanking the display
    lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null

    # Open TCP port for tablet-ui's built-in event streaming server
    iptables -A INPUT -i wlan0 -p tcp --dport "$TCP_PORT" -j ACCEPT 2>/dev/null
}

thaw_framework() {
    # Re-enable Pillow
    lipc-set-prop com.lab126.pillow disableEnablePillow enable 2>/dev/null

    # Allow screensaver again
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null

    # Close TCP port
    iptables -D INPUT -i wlan0 -p tcp --dport "$TCP_PORT" -j ACCEPT 2>/dev/null
}

# ---- Start ----

start_tablet_mode() {
    if [ -f "$MARKER" ]; then
        echo "Tablet mode already active"; return
    fi

    echo "Starting tablet mode"

    # Write our PID into the marker so we can detect when a NEW start has
    # taken over (avoids the stale-process race where the first start's
    # cleanup kills a second session that launched in the meantime).
    MY_ID="$$"
    echo "$MY_ID" > "$MARKER"

    freeze_framework

    # Launch tablet-ui: handles the GTK interface, pen proximity filtering,
    # and TCP event streaming all in one process.  Pass --device explicitly
    # (using the same lexicographic order the host uses) so the streamed
    # events come from the same evdev node whose caps the host will read.
    PEN_DEVICE=$(detect_pen_device)
    if [ -n "$PEN_DEVICE" ]; then
        "$EXT_DIR/bin/tablet-ui" \
            --marker-file "$MARKER" \
            --device "$PEN_DEVICE" \
            --port "$TCP_PORT" &
    else
        echo "Warning: no pen device detected; falling back to tablet-ui auto-detect"
        "$EXT_DIR/bin/tablet-ui" \
            --marker-file "$MARKER" \
            --port "$TCP_PORT" &
    fi
    UI_PID=$!

    echo "Tablet mode running (tablet-ui pid=$UI_PID, port=$TCP_PORT)"

    # Wait until the marker is removed OR taken over by a new instance.
    # tablet-ui removes it when the exit button is tapped.
    # 'stop' action also removes it.
    # A new 'start' overwrites it with a different PID.
    while [ -f "$MARKER" ] && [ "$(cat "$MARKER" 2>/dev/null)" = "$MY_ID" ]; do
        sleep 1
    done

    # Only clean up if we still own the marker (or it was deleted, meaning
    # stop/exit was called).  If a new instance wrote a different PID, it
    # owns the session now — we must not interfere.
    CURRENT_OWNER=$(cat "$MARKER" 2>/dev/null)
    if [ -z "$CURRENT_OWNER" ] || [ "$CURRENT_OWNER" = "$MY_ID" ]; then
        kill "$UI_PID" 2>/dev/null
        do_stop
    fi
}

# ---- Stop ----

do_stop() {
    rm -f "$MARKER"

    # Safety-net cleanup of IPC files.
    # tablet-ui normally removes these itself via app_shutdown(); these
    # rm calls handle the case where the process was killed abnormally.
    rm -f /tmp/tablet-rotation
    rm -f /tmp/tablet-shortcut

    # Kill UI if still running
    pkill -f "tablet-ui" 2>/dev/null

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
