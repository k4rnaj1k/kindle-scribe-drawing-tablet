from __future__ import annotations

"""Main entry point for kindle-tablet.

Connects to a Kindle Scribe via SSH, reads pen/touch input events,
and injects them as tablet input on the host machine.
"""

import argparse
import json
import logging
import math
import signal
import sys
import threading
from pathlib import Path

from .config import Config, KindleConfig, TabletConfig
from .connector import KindleConnector
from .events import ControlCode, PenState, TouchState

log = logging.getLogger("kindle_tablet")

CONFIG_PATH = Path.home() / ".config" / "kindle-tablet" / "config.json"


def load_config(path: Path) -> Config:
    """Load config from JSON file, or return defaults."""
    if not path.exists():
        log.info("No config file found at %s, using defaults.", path)
        return Config()

    with open(path) as f:
        data = json.load(f)

    kindle = KindleConfig(**data.get("kindle", {}))
    tablet = TabletConfig(**data.get("tablet", {}))
    cfg = Config(
        kindle=kindle,
        tablet=tablet,
        mode=data.get("mode", "ssh"),
        pen_device=data.get("pen_device", ""),
        touch_device=data.get("touch_device", ""),
    )
    return cfg


def save_config(cfg: Config, path: Path) -> None:
    """Save config to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "kindle": {
            "host": cfg.kindle.host,
            "port": cfg.kindle.port,
            "username": cfg.kindle.username,
            "password": cfg.kindle.password,
            "key_path": cfg.kindle.key_path,
            "stream_port": cfg.kindle.stream_port,
        },
        "tablet": {
            "kindle_max_x": cfg.tablet.kindle_max_x,
            "kindle_max_y": cfg.tablet.kindle_max_y,
            "kindle_max_pressure": cfg.tablet.kindle_max_pressure,
            "kindle_max_tilt": cfg.tablet.kindle_max_tilt,
            "screen_region": list(cfg.tablet.screen_region),
            "pressure_curve": cfg.tablet.pressure_curve,
            "enable_tilt": cfg.tablet.enable_tilt,
            "enable_touch": cfg.tablet.enable_touch,
        },
        "mode": cfg.mode,
        "pen_device": cfg.pen_device,
        "touch_device": cfg.touch_device,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    log.info("Config saved to %s", path)


def create_input_backend():
    """Create the platform-specific input backend."""
    if sys.platform == "darwin":
        from .input_macos import MacOSInput
        return MacOSInput()
    elif sys.platform == "win32":
        from .input_windows import WindowsInput
        return WindowsInput()
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")


class TabletHandler:
    """Translates Kindle pen/touch events to host input."""

    def __init__(self, config: Config, backend):
        self.config = config
        self.backend = backend
        self._pen_was_touching = False
        self._pen_was_button1 = False
        self._last_touch_y: int | None = None
        self._last_touch_x: int | None = None
        # Store original axis limits for rotation
        self._original_max_x = config.tablet.kindle_max_x
        self._original_max_y = config.tablet.kindle_max_y
        self._rotation = 0  # 0 = portrait, 90 = landscape
        self._compute_mapping()

    def _compute_mapping(self) -> None:
        """Precompute aspect-ratio-preserving mapping parameters.

        Maps the Kindle's full digitizer area to the host screen while
        maintaining proportions (like Wacom 'force proportions' mode).
        """
        tc = self.config.tablet
        kw = tc.kindle_max_x
        kh = tc.kindle_max_y

        # Screen region in pixels
        rx, ry, rw, rh = tc.screen_region
        region_x = rx * self.backend.screen_width
        region_y = ry * self.backend.screen_height
        region_w = rw * self.backend.screen_width
        region_h = rh * self.backend.screen_height

        kindle_aspect = kw / kh
        region_aspect = region_w / region_h if region_h > 0 else 1.0

        if kindle_aspect < region_aspect:
            # Kindle is taller relative to width -- height fills region
            mapped_h = region_h
            mapped_w = region_h * kindle_aspect
        else:
            # Kindle is wider -- width fills region
            mapped_w = region_w
            mapped_h = region_w / kindle_aspect

        self._map_offset_x = region_x + (region_w - mapped_w) / 2
        self._map_offset_y = region_y + (region_h - mapped_h) / 2
        self._map_scale_x = mapped_w / kw if kw > 0 else 1.0
        self._map_scale_y = mapped_h / kh if kh > 0 else 1.0

        log.info("Mapping: kindle %dx%d -> %.0fx%.0f px (offset %.0f,%.0f, scale %.4f)",
                 kw, kh, mapped_w, mapped_h,
                 self._map_offset_x, self._map_offset_y, self._map_scale_x)

    def map_coords(self, kindle_x: int, kindle_y: int) -> tuple[float, float]:
        """Map Kindle digitizer coordinates to host screen coordinates.

        Uses aspect-ratio-preserving mapping so pen movement is uniform
        in all directions (like a real drawing tablet).
        Applies rotation transform when in landscape mode.
        """
        if self._rotation == 90:
            # 90° CW: new_x = y, new_y = max_x - x (same as old daemon transform)
            kindle_x, kindle_y = kindle_y, self._original_max_x - kindle_x

        x = self._map_offset_x + kindle_x * self._map_scale_x
        y = self._map_offset_y + kindle_y * self._map_scale_y
        return x, y

    def map_pressure(self, raw_pressure: int, touching: bool = False) -> float:
        """Map raw pressure to 0.0-1.0 with curve applied.

        When touching, clamps to a minimum floor so Krita registers the stroke.
        """
        tc = self.config.tablet
        normalized = raw_pressure / tc.kindle_max_pressure
        pressure = math.pow(max(normalized, 0.0), tc.pressure_curve)
        if touching and pressure < tc.pressure_floor:
            pressure = tc.pressure_floor
        return pressure

    def map_tilt(self, raw_tilt: int) -> float:
        """Map raw tilt to -1.0 to 1.0."""
        tc = self.config.tablet
        return raw_tilt / tc.kindle_max_tilt

    def on_pen(self, pen: PenState) -> None:
        """Handle a pen state update."""
        if not pen.in_range:
            # Pen left proximity entirely - send leave so macOS clears the device
            if self._pen_was_touching:
                x, y = self.map_coords(pen.x, pen.y)
                self._pen_was_touching = False
            if self._pen_was_button1:
                x, y = self.map_coords(pen.x, pen.y)
                self.backend.button_up(x, y)
                self._pen_was_button1 = False
            x, y = self.map_coords(pen.x, pen.y)
            if hasattr(self.backend, "pen_leave"):
                self.backend.pen_leave(x, y)
            else:
                self.backend.pen_up(x, y)
            return

        x, y = self.map_coords(pen.x, pen.y)
        pressure = self.map_pressure(pen.pressure, touching=pen.touching)
        tilt_x = self.map_tilt(pen.tilt_x) if self.config.tablet.enable_tilt else 0.0
        tilt_y = self.map_tilt(pen.tilt_y) if self.config.tablet.enable_tilt else 0.0
        eraser = pen.eraser

        # Handle stylus button (right-click)
        if pen.button1 and not self._pen_was_button1:
            self.backend.button_down(x, y)
            self._pen_was_button1 = True
        elif not pen.button1 and self._pen_was_button1:
            self.backend.button_up(x, y)
            self._pen_was_button1 = False

        # Handle pen touch (left-click / draw)
        if pen.touching and not self._pen_was_touching:
            if hasattr(self.backend, "pen_down"):
                try:
                    self.backend.pen_down(x, y, pressure, tilt_x, tilt_y, eraser)
                except TypeError:
                    # Windows backend doesn't have eraser param
                    self.backend.pen_down(x, y, pressure, tilt_x, tilt_y)
            self._pen_was_touching = True
        elif not pen.touching and self._pen_was_touching:
            self.backend.pen_up(x, y)
            self._pen_was_touching = False
        else:
            # Hovering or dragging - send move with current pressure
            try:
                self.backend.move(x, y, pressure, tilt_x, tilt_y, eraser)
            except TypeError:
                self.backend.move(x, y, pressure, tilt_x, tilt_y)

    def on_control(self, code: int, value: int) -> None:
        """Handle a control message (rotation change from tablet-ui)."""
        if code == ControlCode.CTRL_ROTATION:
            self._rotation = value
            tc = self.config.tablet
            if value == 90:
                # Landscape: swap effective dimensions for aspect ratio mapping
                tc.kindle_max_x = self._original_max_y
                tc.kindle_max_y = self._original_max_x
                log.info("Rotation: landscape (effective %dx%d)",
                         tc.kindle_max_x, tc.kindle_max_y)
            else:
                # Portrait: restore original
                tc.kindle_max_x = self._original_max_x
                tc.kindle_max_y = self._original_max_y
                log.info("Rotation: portrait (effective %dx%d)",
                         tc.kindle_max_x, tc.kindle_max_y)
            self._compute_mapping()
        elif code == ControlCode.CTRL_DISCONNECT:
            log.info("Tablet daemon disconnected")

    def on_touch(self, touch: TouchState) -> None:
        """Handle touch state update. Single finger = move cursor, two fingers = scroll."""
        active_slots = [s for s in touch.slots.values() if s.active]

        if len(active_slots) == 0:
            self._last_touch_x = None
            self._last_touch_y = None
            return

        if len(active_slots) == 1:
            # Single touch: move cursor
            slot = active_slots[0]
            x, y = self.map_coords(slot.x, slot.y)
            self.backend.move(x, y)
            self._last_touch_x = slot.x
            self._last_touch_y = slot.y

        elif len(active_slots) >= 2:
            # Two-finger scroll
            avg_x = sum(s.x for s in active_slots) / len(active_slots)
            avg_y = sum(s.y for s in active_slots) / len(active_slots)
            if self._last_touch_x is not None and self._last_touch_y is not None:
                dx = int((avg_x - self._last_touch_x) / 10)
                dy = int((avg_y - self._last_touch_y) / 10)
                if dx != 0 or dy != 0:
                    self.backend.scroll(dx, -dy)
            self._last_touch_x = int(avg_x)
            self._last_touch_y = int(avg_y)


def setup_kindle_tablet_mode(connector: KindleConnector) -> None:
    """Set up the Kindle for tablet mode - stop framework, clear screen, show UI."""
    try:
        ssh = connector._ssh
        if ssh:
            # Use the tablet-mode.sh script if deployed, otherwise do it manually
            _, stdout, _ = ssh.exec_command(
                "test -x /mnt/us/extensions/kindle-tablet/bin/tablet-mode.sh && echo ok"
            )
            if stdout.read().decode().strip() == "ok":
                ssh.exec_command(
                    "/mnt/us/extensions/kindle-tablet/bin/tablet-mode.sh start &"
                )
                log.info("Started tablet mode via KUAL extension script.")
            else:
                # Manual fallback: stop framework and clear screen
                ssh.exec_command("stop lab126_gui 2>/dev/null")
                ssh.exec_command(
                    "lipc-set-prop com.lab126.pillow disableEnablePillow disable 2>/dev/null"
                )
                import time
                time.sleep(1)
                ssh.exec_command("eips -c")
                ssh.exec_command('eips 5 20 "Tablet mode active"')
                ssh.exec_command('eips 5 22 "Press Ctrl+C on host to exit"')
                log.info("Kindle framework stopped, screen cleared.")
    except Exception as e:
        log.warning("Could not set up tablet mode on Kindle: %s", e)


def restore_kindle(connector: KindleConnector) -> None:
    """Restore the Kindle UI after tablet mode."""
    try:
        ssh = connector._ssh
        if ssh:
            ssh.exec_command(
                "/mnt/us/extensions/kindle-tablet/bin/tablet-mode.sh stop 2>/dev/null; "
                "start lab126_gui 2>/dev/null; "
                "lipc-set-prop com.lab126.pillow disableEnablePillow enable 2>/dev/null"
            )
            log.info("Kindle UI restored.")
    except Exception as e:
        log.warning("Could not restore Kindle UI: %s", e)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Use your Kindle Scribe as a drawing tablet"
    )
    parser.add_argument("--host", help="Kindle IP address")
    parser.add_argument("--port", type=int, help="SSH port (default: 22)")
    parser.add_argument("--user", help="SSH username (default: root)")
    parser.add_argument("--password", help="SSH password")
    parser.add_argument("--key", help="SSH private key path")
    parser.add_argument("--mode", choices=["ssh", "tcp"],
                        help="Connection mode (default: ssh)")
    parser.add_argument("--pen-device", help="Pen input device path on Kindle")
    parser.add_argument("--touch-device", help="Touch input device path on Kindle")
    parser.add_argument("--no-touch", action="store_true", help="Disable touch input")
    parser.add_argument("--pressure-curve", type=float,
                        help="Pressure curve (0.1-3.0, default: 0.7)")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                        help=f"Config file path (default: {CONFIG_PATH})")
    parser.add_argument("--save-config", action="store_true",
                        help="Save current settings to config file")
    parser.add_argument("--list-devices", action="store_true",
                        help="List input devices on the Kindle and exit")
    parser.add_argument("--clear-screen", action="store_true",
                        help="Clear the Kindle screen on connect")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Load config
    cfg = load_config(args.config)

    # Apply CLI overrides
    if args.host:
        cfg.kindle.host = args.host
    if args.port:
        cfg.kindle.port = args.port
    if args.user:
        cfg.kindle.username = args.user
    if args.password:
        cfg.kindle.password = args.password
    if args.key:
        cfg.kindle.key_path = args.key
    if args.mode:
        cfg.mode = args.mode
    if args.pen_device:
        cfg.pen_device = args.pen_device
    if args.touch_device:
        cfg.touch_device = args.touch_device
    if args.no_touch:
        cfg.tablet.enable_touch = False
    if args.pressure_curve is not None:
        cfg.tablet.pressure_curve = args.pressure_curve

    if args.save_config:
        save_config(cfg, args.config)

    # Connect to Kindle
    connector = KindleConnector(cfg)
    try:
        connector.connect()
    except Exception as e:
        log.error("Failed to connect to Kindle: %s", e)
        sys.exit(1)

    # List devices mode
    if args.list_devices:
        devices = connector.detect_devices()
        print("\nInput devices on Kindle:")
        for evname, devname in devices.items():
            print(f"  /dev/input/{evname} -> {devname}")
        connector.stop()
        return

    # Set up tablet mode on Kindle
    if args.clear_screen:
        setup_kindle_tablet_mode(connector)

    # Create input backend
    try:
        backend = create_input_backend()
    except Exception as e:
        log.error("Failed to create input backend: %s", e)
        connector.stop()
        sys.exit(1)

    # Create handler
    handler = TabletHandler(cfg, backend)

    # Wire up callbacks
    connector.on_pen = handler.on_pen
    connector.on_touch = handler.on_touch if cfg.tablet.enable_touch else None
    connector.on_control = handler.on_control

    # Start streaming
    stop_event = threading.Event()

    def on_signal(signum, frame):
        log.info("Shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        connector.start_streaming()
        print("\n  Kindle Tablet active!")
        print(f"  Mode: {cfg.mode} | Pen: {cfg.pen_device}")
        if cfg.touch_device:
            print(f"  Touch: {cfg.touch_device}")
        print("  Press Ctrl+C to stop.\n")

        # Save config with auto-detected values
        if args.save_config:
            save_config(cfg, args.config)

        stop_event.wait()
    except Exception as e:
        log.error("Error: %s", e)
    finally:
        # Restore the Kindle UI if we set up tablet mode
        if args.clear_screen:
            restore_kindle(connector)
        connector.stop()
        print("Kindle Tablet stopped.")


if __name__ == "__main__":
    main()
