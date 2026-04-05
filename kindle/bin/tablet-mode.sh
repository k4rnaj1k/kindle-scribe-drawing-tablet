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
DAEMON_PORT=8234

# ---- Framework control ----

freeze_framework() {
    # Disable Pillow (status bar overlay)
    lipc-set-prop com.lab126.pillow disableEnablePillow disable 2>/dev/null

    # Prevent screensaver from blanking the display
    lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null

    # Open TCP port for tablet-daemon
    iptables -A INPUT -i wlan0 -p tcp --dport "$DAEMON_PORT" -j ACCEPT 2>/dev/null
}

thaw_framework() {
    # Re-enable Pillow
    lipc-set-prop com.lab126.pillow disableEnablePillow enable 2>/dev/null

    # Allow screensaver again
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null

    # Close TCP port
    iptables -D INPUT -i wlan0 -p tcp --dport "$DAEMON_PORT" -j ACCEPT 2>/dev/null
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

    # Auto-detect pen device
    PEN_DEVICE=""
    for name_file in /sys/class/input/event*/device/name; do
        name=$(cat "$name_file" 2>/dev/null | tr '[:upper:]' '[:lower:]')
        case "$name" in
            *wacom*|*stylus*|*ntx_event*|*digitizer*|*pen*)
                evdev=$(echo "$name_file" | sed 's|/sys/class/input/\(event[0-9]*\)/.*|\1|')
                PEN_DEVICE="/dev/input/$evdev"
                break
                ;;
        esac
    done

    if [ -n "$PEN_DEVICE" ]; then
        "$EXT_DIR/bin/tablet-daemon" "$PEN_DEVICE" "$DAEMON_PORT" \
            > /tmp/tablet-daemon.log 2>&1 &
        DAEMON_PID=$!
        echo "tablet-daemon started (pid=$DAEMON_PID, device=$PEN_DEVICE, port=$DAEMON_PORT)"
    else
        echo "WARNING: could not auto-detect pen device, tablet-daemon not started"
        DAEMON_PID=""
    fi

    # Launch the GTK UI app (shows exit button)
    "$EXT_DIR/bin/tablet-ui" --marker-file "$MARKER" &
    GTK_PID=$!

    echo "Tablet mode running (GTK UI pid=$GTK_PID)"

    # Wait until the marker is removed.
    # tablet-ui removes it when exit button is tapped.
    # 'stop' action also removes it.
    while [ -f "$MARKER" ]; do sleep 1; done

    # Clean up GTK app and daemon if still running
    kill "$GTK_PID" 2>/dev/null
    [ -n "$DAEMON_PID" ] && kill "$DAEMON_PID" 2>/dev/null

    do_stop
}

# ---- Stop ----

do_stop() {
    rm -f "$MARKER"
    rm -f /tmp/tablet-rotation

    # Kill UI and daemon if still running
    pkill -f "tablet-ui" 2>/dev/null
    pkill -f "tablet-daemon" 2>/dev/null

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
