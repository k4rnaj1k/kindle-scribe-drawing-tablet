from __future__ import annotations

"""Kindle connection manager - SSH and TCP streaming modes."""

import copy
import logging
import queue
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
                 on_control: Callable | None = None):
        self.config = config
        self.on_pen = on_pen
        self.on_control = on_control
        self._ssh: paramiko.SSHClient | None = None
        self._running = False
        self._threads: list[threading.Thread] = []
        self.parser = EventParser(arch_bits=32)
        self._dispatch_queue: queue.Queue = queue.Queue()
        # Monitor channels kept so stop() can close them and unblock recv()
        self._rotation_channel = None
        self._shortcut_channel = None

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

        # Disable Nagle on the SSH transport socket to reduce latency for
        # small pen event packets.
        try:
            transport = self._ssh.get_transport()
            if transport is not None:
                transport.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception as e:
            log.warning("Could not set TCP_NODELAY on SSH transport: %s", e)

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

        # Read device capabilities
        self.update_config_from_device(self.config.pen_device)

        self._running = True

        t = threading.Thread(target=self._dispatch_loop, daemon=True, name="dispatcher")
        t.start()
        self._threads.append(t)

        if self.config.mode == "tcp":
            self._start_tcp_streaming()
        else:
            self._start_ssh_streaming()

    def _start_ssh_streaming(self) -> None:
        """Stream events from the Kindle over SSH using raw cat."""
        pen_device = self.config.pen_device

        t = threading.Thread(target=self._ssh_read_loop, args=(pen_device, "pen"),
                             daemon=True, name="pen-reader")
        t.start()
        self._threads.append(t)

        # Monitor rotation changes from tablet-ui
        self._start_rotation_monitor()
        # Monitor shortcut button presses from tablet-ui
        self._start_shortcut_monitor()

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

        last_recv = 0.0
        last_report = time.monotonic()
        max_recv_gap = 0.0
        max_recv_bytes = 0
        recvs = 0
        try:
            while self._running:
                data = channel.recv(parser.event_size * 64)
                now = time.monotonic()
                if not data:
                    if not self._running:
                        break
                    log.warning("SSH channel for %s closed", device_type)
                    break

                if last_recv:
                    gap = (now - last_recv) * 1000.0
                    if gap > max_recv_gap:
                        max_recv_gap = gap
                last_recv = now
                recvs += 1
                if len(data) > max_recv_bytes:
                    max_recv_bytes = len(data)
                if now - last_report >= 1.0:
                    log.debug("reader[%s]: %d recvs, max_gap=%.1fms, max_bytes=%d",
                              device_type, recvs, max_recv_gap, max_recv_bytes)
                    last_report = now
                    max_recv_gap = 0.0
                    max_recv_bytes = 0
                    recvs = 0

                events = parser.feed(data)
                for ev in events:
                    if isinstance(ev, tuple) and ev[0] == "control":
                        self._dispatch_queue.put(("control", ev[1], ev[2]))
                    elif ev == "pen":
                        self._dispatch_queue.put(("pen", copy.copy(parser.pen)))
        except Exception as e:
            if self._running:
                log.error("Error reading %s: %s", device_type, e)
        finally:
            channel.close()

    def _dispatch_loop(self) -> None:
        """Dispatch queued events to callbacks, coalescing redundant move events.

        Runs in its own thread so HID writes never block the reader thread.
        Consecutive pen events with the same transition state (in_range/touching/
        buttons unchanged) are collapsed to the latest position, preventing the
        freeze-then-burst behaviour caused by SSH buffers filling up while the
        HID write was slow.
        """
        # Diagnostics: track max queue depth, slow HID writes, and gaps between
        # dispatched events. Log a summary once per second.
        last_report = time.monotonic()
        max_qsize = 0
        slow_writes = 0
        max_write_ms = 0.0
        coalesced = 0
        dispatched = 0
        last_dispatch_time = 0.0
        max_gap_ms = 0.0

        while self._running:
            try:
                item = self._dispatch_queue.get(timeout=0.1)
            except queue.Empty:
                now = time.monotonic()
                if now - last_report >= 1.0:
                    if dispatched > 0:
                        log.debug(
                            "dispatch: %d events, max_q=%d, coalesced=%d, "
                            "max_write=%.1fms, slow_writes=%d, max_gap=%.1fms",
                            dispatched, max_qsize, coalesced,
                            max_write_ms, slow_writes, max_gap_ms)
                    last_report = now
                    max_qsize = 0
                    slow_writes = 0
                    max_write_ms = 0.0
                    coalesced = 0
                    dispatched = 0
                    max_gap_ms = 0.0
                continue

            qsize = self._dispatch_queue.qsize()
            if qsize > max_qsize:
                max_qsize = qsize

            ev_type = item[0]

            if ev_type == "pen":
                pen_state = item[1]
                # Coalesce trailing move events: keep draining the queue while the
                # next item is also a pen event with the same transition state.
                while True:
                    try:
                        next_item = self._dispatch_queue.get_nowait()
                    except queue.Empty:
                        break
                    if (next_item[0] == "pen"
                            and next_item[1].touching == pen_state.touching
                            and next_item[1].in_range == pen_state.in_range
                            and next_item[1].button1 == pen_state.button1
                            and next_item[1].eraser == pen_state.eraser):
                        # Pure move/pressure update — drop the older one
                        pen_state = next_item[1]
                        coalesced += 1
                    else:
                        # State transition or different event type — put it back
                        self._dispatch_queue.put(next_item)
                        break
                if self.on_pen:
                    t0 = time.monotonic()
                    self.on_pen(pen_state)
                    elapsed_ms = (time.monotonic() - t0) * 1000.0
                    if elapsed_ms > max_write_ms:
                        max_write_ms = elapsed_ms
                    if elapsed_ms > 5.0:
                        slow_writes += 1
                    dispatched += 1
                    if last_dispatch_time:
                        gap_ms = (t0 - last_dispatch_time) * 1000.0
                        if gap_ms > max_gap_ms:
                            max_gap_ms = gap_ms
                    last_dispatch_time = t0

            elif ev_type == "control":
                if self.on_control:
                    self.on_control(item[1], item[2])

            now = time.monotonic()
            if now - last_report >= 1.0:
                if dispatched > 0:
                    log.debug(
                        "dispatch: %d events, max_q=%d, coalesced=%d, "
                        "max_write=%.1fms, slow_writes=%d, max_gap=%.1fms",
                        dispatched, max_qsize, coalesced,
                        max_write_ms, slow_writes, max_gap_ms)
                last_report = now
                max_qsize = 0
                slow_writes = 0
                max_write_ms = 0.0
                coalesced = 0
                dispatched = 0
                max_gap_ms = 0.0

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
        log.info("Starting rotation monitor (tailing /tmp/tablet-rotation)")

        # Read current rotation before starting tail so we pick up any value
        # that was written before we connected.
        try:
            _, stdout, _ = self._ssh.exec_command(
                'cat /tmp/tablet-rotation 2>/dev/null | tail -n1'
            )
            initial = stdout.read().decode().strip()
            if initial:
                try:
                    last_value = int(initial)
                    if self.on_control:
                        from .events import ControlCode
                        self.on_control(ControlCode.CTRL_ROTATION, last_value)
                except ValueError:
                    pass
        except Exception:
            pass

        cmd = 'touch /tmp/tablet-rotation && tail -f /tmp/tablet-rotation'
        transport = self._ssh.get_transport()
        channel = transport.open_session()
        channel.exec_command(cmd)
        self._rotation_channel = channel

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

    def _start_shortcut_monitor(self) -> None:
        """Monitor /tmp/tablet-shortcut for shortcut button presses written by tablet-ui."""
        t = threading.Thread(target=self._shortcut_monitor_loop,
                             daemon=True, name="shortcut-monitor")
        t.start()
        self._threads.append(t)

    def _shortcut_monitor_loop(self) -> None:
        """Tail /tmp/tablet-shortcut via SSH and dispatch CTRL_SHORTCUT control events."""
        from .events import ControlCode

        log.info("Starting shortcut monitor (tailing /tmp/tablet-shortcut)")

        cmd = '> /tmp/tablet-shortcut && tail -f /tmp/tablet-shortcut'
        transport = self._ssh.get_transport()
        channel = transport.open_session()
        channel.exec_command(cmd)
        self._shortcut_channel = channel

        buf = b""
        try:
            while self._running:
                data = channel.recv(256)
                if not data:
                    if not self._running:
                        break
                    log.warning("Shortcut monitor channel closed")
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        shortcut_id = int(line)
                        log.info("Shortcut triggered: %d", shortcut_id)
                        self._dispatch_queue.put(
                            ("control", ControlCode.CTRL_SHORTCUT, shortcut_id)
                        )
                    except ValueError:
                        pass
        except Exception as e:
            if self._running:
                log.error("Shortcut monitor error: %s", e)
        finally:
            channel.close()

    def _start_tcp_streaming(self) -> None:
        """Connect to tablet-ui's built-in TCP event server on the Kindle."""
        stream_port = self.config.kindle.stream_port

        # tablet-ui now handles both the GTK interface and TCP event streaming
        # in a single process (tablet-daemon was merged into it).  If tablet-ui
        # is not already running (e.g. launched by tablet-mode.sh / KUAL), start
        # it with --port so the TCP server binds on the expected port.
        log.info("Ensuring tablet-ui is running on Kindle (port %d)...", stream_port)
        self._ssh.exec_command(
            f"pgrep -f 'tablet-ui' > /dev/null 2>&1 || "
            f"nohup /mnt/us/extensions/kindle-tablet/bin/tablet-ui "
            f"--port {stream_port} >> /tmp/tablet-ui.log 2>&1 &"
        )
        time.sleep(1)  # Give the server a moment to start if it wasn't running

        t = threading.Thread(target=self._tcp_read_loop, daemon=True, name="tcp-reader")
        t.start()
        self._threads.append(t)

        self._start_rotation_monitor()
        self._start_shortcut_monitor()

    def _tcp_read_loop(self) -> None:
        """Read events from tablet-ui's TCP event server."""
        host = self.config.kindle.host
        port = self.config.kindle.stream_port

        log.info("Connecting to tablet-ui TCP server at %s:%d...", host, port)
        retries = 5
        sock = None
        for attempt in range(retries):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.settimeout(5)
                s.connect((host, port))
                s.settimeout(None)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock = s
                log.info("Connected to tablet-ui TCP server.")
                break
            except (ConnectionRefusedError, socket.timeout):
                s.close()
                log.warning("Connection attempt %d/%d failed, retrying...",
                            attempt + 1, retries)
                time.sleep(1)

        if sock is None:
            log.error("Could not connect to tablet-ui TCP server")
            return

        parser = self.parser
        last_recv = 0.0
        last_report = time.monotonic()
        max_recv_gap = 0.0
        max_recv_bytes = 0
        recvs = 0
        try:
            while self._running:
                data = sock.recv(parser.event_size * 64)
                now = time.monotonic()
                if not data:
                    break

                if last_recv:
                    gap = (now - last_recv) * 1000.0
                    if gap > max_recv_gap:
                        max_recv_gap = gap
                last_recv = now
                recvs += 1
                if len(data) > max_recv_bytes:
                    max_recv_bytes = len(data)
                if now - last_report >= 1.0:
                    log.debug("reader[tcp]: %d recvs, max_gap=%.1fms, max_bytes=%d",
                              recvs, max_recv_gap, max_recv_bytes)
                    last_report = now
                    max_recv_gap = 0.0
                    max_recv_bytes = 0
                    recvs = 0

                events = parser.feed(data)
                for ev in events:
                    if ev == "pen":
                        self._dispatch_queue.put(("pen", copy.copy(parser.pen)))
        except Exception as e:
            if self._running:
                log.error("TCP read error: %s", e)
        finally:
            sock.close()

    def stop(self) -> None:
        """Stop streaming and disconnect."""
        self._running = False

        # Close monitor channels first — this unblocks the recv() calls in the
        # monitor loops immediately, so the threads exit cleanly without waiting
        # for the 3-second join timeout.
        for ch_attr in ("_rotation_channel", "_shortcut_channel"):
            ch = getattr(self, ch_attr, None)
            if ch is not None:
                try:
                    ch.close()
                except Exception:
                    pass
                setattr(self, ch_attr, None)

        # Kill the remote tail processes on the Kindle so they don't linger
        # after the SSH session ends (dropbear doesn't reliably send SIGHUP).
        if self._ssh:
            try:
                self._ssh.exec_command(
                    "pkill -f 'tail.*tablet-rotation' 2>/dev/null; "
                    "pkill -f 'tail.*tablet-shortcut' 2>/dev/null"
                )
            except Exception:
                pass

        # If in TCP mode and we started tablet-ui ourselves, stop it
        if self.config.mode == "tcp" and self._ssh:
            try:
                self._ssh.exec_command("pkill -f 'tablet-ui' 2>/dev/null")
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
