#!/bin/sh
# tablet-mode.sh - Kindle Tablet Mode
#
# Uses the KOReader approach: SIGSTOP the awesome window manager to freeze
# all framework rendering, then write directly to the framebuffer for our UI.
# No watchdog needed -- awesome can't redraw while stopped.
#
# On exit, SIGCONT awesome to resume normal Kindle operation.
#
# Usage: tablet-mode.sh [start|stop|status] [device]

ACTION="${1:-start}"
DEVICE="${2:-auto}"
MARKER="/tmp/tablet-mode-active"
EXT_DIR="/mnt/us/extensions/kindle-tablet"
FB_DUMP="/tmp/tablet-fb-dump"

# ---- Device detection ----

detect_pen_device() {
    for dev in /sys/class/input/event*/device; do
        evdev=$(basename $(dirname "$dev"))
        name=$(cat "$dev/name" 2>/dev/null)
        case "$name" in
            *[Ww]acom*|*[Ss]tylus*|*[Pp]en*|*[Dd]igitizer*|*ntx_event*)
                echo "/dev/input/$evdev"; return ;;
        esac
    done
    echo "/dev/input/event1"
}

detect_touch_device() {
    for dev in /sys/class/input/event*/device; do
        evdev=$(basename $(dirname "$dev"))
        name=$(cat "$dev/name" 2>/dev/null)
        case "$name" in
            *[Tt]ouch*|*cyttsp*|*[Cc]apacitive*|*[Ff]inger*|*[Mm][Tt]*)
                echo "/dev/input/$evdev"; return ;;
        esac
    done
    echo ""
}

# ---- Framework control (KOReader approach) ----

freeze_framework() {
    # Disable Pillow (status bar overlay) via lipc
    lipc-set-prop com.lab126.pillow disableEnablePillow disable 2>/dev/null

    # SIGSTOP awesome -- freezes all framework UI rendering instantly.
    # The framework process tree stays alive, KUAL stays alive, but nothing
    # redraws the screen. This is exactly what KOReader does.
    killall -STOP awesome 2>/dev/null

    # Also stop powerd screensaver to prevent it from blanking the display
    lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null
}

thaw_framework() {
    # Resume awesome -- framework picks up right where it left off
    killall -CONT awesome 2>/dev/null

    # Re-enable Pillow
    lipc-set-prop com.lab126.pillow disableEnablePillow enable 2>/dev/null

    # Allow screensaver again
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null
}

# ---- Framebuffer UI ----

# Save current framebuffer so we can restore it on exit
save_framebuffer() {
    if [ -e /dev/fb0 ]; then
        cp /dev/fb0 "$FB_DUMP" 2>/dev/null
    fi
}

restore_framebuffer() {
    if [ -f "$FB_DUMP" ] && [ -e /dev/fb0 ]; then
        cp "$FB_DUMP" /dev/fb0 2>/dev/null
        rm -f "$FB_DUMP"
        # Trigger a full e-ink refresh if possible
        if [ -x /var/tmp/fbink ]; then
            /var/tmp/fbink -s top=0,left=0,width=0,height=0 -f 2>/dev/null
        elif command -v eips >/dev/null 2>&1; then
            eips '' 2>/dev/null
        fi
    fi
}

# ---- Start ----

start_tablet_mode() {
    if [ -f "$MARKER" ]; then
        echo "Tablet mode already active"; return
    fi

    [ "$DEVICE" = "auto" ] && DEVICE=$(detect_pen_device)
    TOUCH_DEVICE=$(detect_touch_device)

    echo "Starting tablet mode"
    echo "  Pen:   $DEVICE"
    echo "  Touch: $TOUCH_DEVICE"

    # Write marker before anything else so stop works even if we crash
    echo "$DEVICE" > "$MARKER"

    # Save framebuffer, freeze framework
    save_framebuffer
    freeze_framework

    echo "Tablet mode running."

    # Draw a simple status screen using eips (always available).
    # The host-launched tablet-daemon will overwrite this with buttons
    # once it connects over SSH.
    eips -c 2>/dev/null
    sleep 1
    eips -c 2>/dev/null
    eips 10 5 "KINDLE TABLET MODE" 2>/dev/null
    eips 5 8 "Waiting for host connection..." 2>/dev/null
    eips 5 10 "Run: kindle-tablet --host <IP>" 2>/dev/null

    # Wait until the marker is removed.
    # tablet-daemon removes it when exit button is tapped.
    # 'stop' action also removes it.
    while [ -f "$MARKER" ]; do sleep 1; done

    do_stop
}

# ---- Stop ----

do_stop() {
    rm -f "$MARKER"

    # Kill daemon if still running
    pkill -f "tablet-daemon" 2>/dev/null

    # Restore framebuffer and thaw framework
    restore_framebuffer
    thaw_framework

    # Trigger a screen refresh to get back to normal Kindle UI
    if command -v eips >/dev/null 2>&1; then
        eips '' 2>/dev/null
    fi
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
        if [ -f "$MARKER" ]; then echo "active: $(cat $MARKER)"
        else echo "inactive"; fi ;;
    *) echo "Usage: $0 [start|stop|status] [device]"; exit 1 ;;
esac
