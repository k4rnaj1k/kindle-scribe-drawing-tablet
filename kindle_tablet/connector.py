from __future__ import annotations

"""Kindle connection manager - SSH and TCP streaming modes."""

import logging
import socket
import threading
import time
from pathlib import Path
from typing import Callable

import paramiko

from .config import Config
from .events import EventParser

log = logging.getLogger(__name__)

# Script to detect input devices on the Kindle
DETECT_DEVICES_SCRIPT = r"""
for dev in /sys/class/input/event*/device; do
    evdev=$(basename $(dirname "$dev"))
    name=$(cat "$dev/name" 2>/dev/null || echo "unknown")
    echo "$evdev:$name"
done
"""

# Script to read digitizer capabilities (axis ranges)
READ_CAPS_SCRIPT = r"""
DEVICE="$1"
for axis in 00 01 18 1a 1b; do
    absfile="/sys/class/input/${DEVICE}/device/abs_${axis}"
    if [ -f "${absfile}/max" ]; then
        echo "${axis}:$(cat ${absfile}/min):$(cat ${absfile}/max)"
    fi
done
"""


class KindleConnector:
    """Manages connection to a Kindle Scribe and streams input events."""

    def __init__(self, config: Config, on_pen: Callable | None = None,
                 on_touch: Callable | None = None,
                 on_control: Callable | None = None):
        self.config = config
        self.on_pen = on_pen
        self.on_touch = on_touch
        self.on_control = on_control
        self._ssh: paramiko.SSHClient | None = None
        self._running = False
        self._threads: list[threading.Thread] = []
        self.parser = EventParser(arch_bits=32)

    def connect(self) -> None:
        """Establish SSH connection to the Kindle."""
        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = {
            "hostname": self.config.kindle.host,
            "port": self.config.kindle.port,
            "username": self.config.kindle.username,
            "timeout": 10,
        }

        if self.config.kindle.key_path:
            connect_kwargs["key_filename"] = self.config.kindle.key_path
        elif self.config.kindle.password:
            connect_kwargs["password"] = self.config.kindle.password
        else:
            # Try default key locations
            default_key = Path.home() / ".ssh" / "id_rsa"
            if default_key.exists():
                connect_kwargs["key_filename"] = str(default_key)

        log.info("Connecting to Kindle at %s:%d...", self.config.kindle.host,
                 self.config.kindle.port)
        self._ssh.connect(**connect_kwargs)
        log.info("Connected to Kindle.")

    def detect_devices(self) -> dict[str, str]:
        """Detect input devices on the Kindle. Returns {event_name: device_name}."""
        if not self._ssh:
            raise RuntimeError("Not connected")

        _, stdout, _ = self._ssh.exec_command(DETECT_DEVICES_SCRIPT)
        devices = {}
        for line in stdout.read().decode().strip().split("\n"):
            if ":" in line:
                evname, devname = line.split(":", 1)
                devices[evname] = devname
                log.info("Found device: /dev/input/%s -> %s", evname, devname)
        return devices

    def auto_detect_pen_device(self) -> str:
        """Try to auto-detect the pen digitizer device path."""
        devices = self.detect_devices()
        # Look for Wacom-like device names
        pen_keywords = ["wacom", "stylus", "pen", "digitizer", "ntx_event"]
        for evname, devname in devices.items():
            lower = devname.lower()
            for kw in pen_keywords:
                if kw in lower:
                    path = f"/dev/input/{evname}"
                    log.info("Auto-detected pen device: %s (%s)", path, devname)
                    return path

        # Fallback: list all and let user choose
        log.warning("Could not auto-detect pen device. Available devices:")
        for evname, devname in devices.items():
            log.warning("  /dev/input/%s -> %s", evname, devname)
        raise RuntimeError(
            "Could not auto-detect pen device. Set pen_device in config. "
            f"Available: {devices}"
        )

    def auto_detect_touch_device(self) -> str:
        """Try to auto-detect the touch device path."""
        devices = self.detect_devices()
        touch_keywords = ["touch", "cyttsp", "capacitive", "mt", "pt_mt", "finger"]
        for evname, devname in devices.items():
            lower = devname.lower()
            for kw in touch_keywords:
                if kw in lower:
                    path = f"/dev/input/{evname}"
                    log.info("Auto-detected touch device: %s (%s)", path, devname)
                    return path
        log.warning("Could not auto-detect touch device.")
        return ""

    def read_device_caps(self, device_path: str) -> dict[str, tuple[int, int]]:
        """Read axis min/max from the device. Returns {axis_name: (min, max)}."""
        if not self._ssh:
            raise RuntimeError("Not connected")

        evname = device_path.split("/")[-1]
        _, stdout, _ = self._ssh.exec_command(
            f'for axis in 00 01 18 1a 1b; do '
            f'  absfile="/sys/class/input/{evname}/device/abs_${{axis}}"; '
            f'  if [ -f "${{absfile}}/max" ]; then '
            f'    echo "${{axis}}:$(cat ${{absfile}}/min):$(cat ${{absfile}}/max)"; '
            f'  fi; '
            f'done'
        )
        axis_map = {"00": "x", "01": "y", "18": "pressure", "1a": "tilt_x", "1b": "tilt_y"}
        caps = {}
        for line in stdout.read().decode().strip().split("\n"):
            parts = line.strip().split(":")
            if len(parts) == 3:
                axis_hex, min_val, max_val = parts
                name = axis_map.get(axis_hex, axis_hex)
                caps[name] = (int(min_val), int(max_val))
                log.info("  %s: min=%s max=%s", name, min_val, max_val)
        return caps

    def update_config_from_device(self, device_path: str) -> None:
        """Read device capabilities and update config accordingly."""
        try:
            caps = self.read_device_caps(device_path)
            tc = self.config.tablet
            if "x" in caps:
                tc.kindle_max_x = caps["x"][1]
            if "y" in caps:
                tc.kindle_max_y = caps["y"][1]
            if "pressure" in caps:
                tc.kindle_max_pressure = caps["pressure"][1]
            log.info("Updated config from device caps: max_x=%d, max_y=%d, max_pressure=%d",
                     tc.kindle_max_x, tc.kindle_max_y, tc.kindle_max_pressure)
        except Exception as e:
            log.warning("Could not read device caps, using defaults: %s", e)

    def start_streaming(self) -> None:
        """Start streaming input events from the Kindle."""
        if not self._ssh:
            raise RuntimeError("Not connected")

        # Auto-detect devices if not configured
        if not self.config.pen_device:
            self.config.pen_device = self.auto_detect_pen_device()

        if not self.config.touch_device and self.config.tablet.enable_touch:
            self.config.touch_device = self.auto_detect_touch_device()

        # Read device capabilities
        self.update_config_from_device(self.config.pen_device)

        self._running = True

        if self.config.mode == "tcp":
            self._start_tcp_streaming()
        else:
            self._start_ssh_streaming()

    def _start_ssh_streaming(self) -> None:
        """Stream events from the Kindle over SSH using raw cat."""
        pen_device = self.config.pen_device
        touch_device = self.config.touch_device

        t = threading.Thread(target=self._ssh_read_loop, args=(pen_device, "pen"),
                             daemon=True, name="pen-reader")
        t.start()
        self._threads.append(t)

        if touch_device and self.config.tablet.enable_touch:
            touch_parser = EventParser(arch_bits=32)
            t2 = threading.Thread(target=self._ssh_read_loop,
                                  args=(touch_device, "touch", touch_parser),
                                  daemon=True, name="touch-reader")
            t2.start()
            self._threads.append(t2)

        # Monitor rotation changes from tablet-ui
        self._start_rotation_monitor()

    def _ssh_read_loop(self, device: str, device_type: str,
                       parser: EventParser | None = None) -> None:
        """Read events from an SSH channel via cat on the evdev device."""
        if parser is None:
            parser = self.parser

        cmd = f"cat {device}"
        log.info("Starting SSH stream for %s: %s", device_type, cmd)
        transport = self._ssh.get_transport()
        channel = transport.open_session()
        channel.exec_command(cmd)

        try:
            while self._running:
                data = channel.recv(parser.event_size * 64)
                if not data:
                    if not self._running:
                        break
                    log.warning("SSH channel for %s closed", device_type)
                    break

                events = parser.feed(data)
                for ev in events:
                    if isinstance(ev, tuple) and ev[0] == "control":
                        if self.on_control:
                            self.on_control(ev[1], ev[2])
                    elif ev == "pen" and self.on_pen:
                        self.on_pen(parser.pen)
                    elif ev == "touch" and self.on_touch:
                        self.on_touch(parser.touch)
        except Exception as e:
            if self._running:
                log.error("Error reading %s: %s", device_type, e)
        finally:
            channel.close()

    def _start_rotation_monitor(self) -> None:
        """Monitor /tmp/tablet-rotation for changes written by tablet-ui."""
        t = threading.Thread(target=self._rotation_monitor_loop,
                             daemon=True, name="rotation-monitor")
        t.start()
        self._threads.append(t)

    def _rotation_monitor_loop(self) -> None:
        """Poll /tmp/tablet-rotation via a single SSH channel for rotation changes."""
        from .events import ControlCode

        last_value = None
        log.info("Starting rotation monitor (polling /tmp/tablet-rotation)")

        # Use a single persistent channel that runs a shell loop,
        # avoiding repeated exec_command calls which aren't thread-safe.
        cmd = (
            'while true; do '
            'cat /tmp/tablet-rotation 2>/dev/null | tail -n1; '
            'sleep 1; '
            'done'
        )
        transport = self._ssh.get_transport()
        channel = transport.open_session()
        channel.exec_command(cmd)

        buf = b""
        try:
            while self._running:
                data = channel.recv(256)
                if not data:
                    if not self._running:
                        break
                    log.warning("Rotation monitor channel closed")
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        angle = int(line)
                        if angle != last_value:
                            last_value = angle
                            log.info("Rotation file changed: %d", angle)
                            if self.on_control:
                                self.on_control(ControlCode.CTRL_ROTATION, angle)
                    except ValueError:
                        pass
        except Exception as e:
            if self._running:
                log.error("Rotation monitor error: %s", e)
        finally:
            channel.close()

    def _start_tcp_streaming(self) -> None:
        """Connect to the tablet-server running on the Kindle via TCP."""
        # First, start the server on the Kindle
        stream_port = self.config.kindle.stream_port
        pen_device = self.config.pen_device

        log.info("Starting tablet-server on Kindle (port %d)...", stream_port)
        # Kill any existing server, then start a new one
        self._ssh.exec_command(
            f"pkill -f 'tablet-server' 2>/dev/null; "
            f"nohup /mnt/us/extensions/kindle-tablet/bin/tablet-server.sh "
            f"{pen_device} {stream_port} > /dev/null 2>&1 &"
        )
        time.sleep(1)  # Give the server a moment to start

        t = threading.Thread(target=self._tcp_read_loop, daemon=True, name="tcp-reader")
        t.start()
        self._threads.append(t)

    def _tcp_read_loop(self) -> None:
        """Read events from TCP socket."""
        host = self.config.kindle.host
        port = self.config.kindle.stream_port

        log.info("Connecting to tablet-server at %s:%d...", host, port)
        retries = 5
        sock = None
        for attempt in range(retries):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((host, port))
                sock.settimeout(None)
                log.info("Connected to tablet-server.")
                break
            except (ConnectionRefusedError, socket.timeout):
                log.warning("Connection attempt %d/%d failed, retrying...",
                            attempt + 1, retries)
                time.sleep(1)

        if sock is None:
            log.error("Could not connect to tablet-server")
            return

        parser = self.parser
        try:
            while self._running:
                data = sock.recv(parser.event_size * 64)
                if not data:
                    break
                events = parser.feed(data)
                for ev in events:
                    if ev == "pen" and self.on_pen:
                        self.on_pen(parser.pen)
                    elif ev == "touch" and self.on_touch:
                        self.on_touch(parser.touch)
        except Exception as e:
            if self._running:
                log.error("TCP read error: %s", e)
        finally:
            sock.close()

    def stop(self) -> None:
        """Stop streaming and disconnect."""
        self._running = False

        # If in TCP mode, kill the server on the Kindle
        if self.config.mode == "tcp" and self._ssh:
            try:
                self._ssh.exec_command("pkill -f 'tablet-server' 2>/dev/null")
            except Exception:
                pass

        for t in self._threads:
            t.join(timeout=3)
        self._threads.clear()

        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None
        log.info("Disconnected from Kindle.")
