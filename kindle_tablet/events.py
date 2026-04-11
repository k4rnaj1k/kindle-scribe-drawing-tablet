from __future__ import annotations

"""Linux input event parser for Kindle digitizer.

Parses raw bytes from /dev/input/eventN into structured pen events.
The Kindle Scribe runs 32-bit ARM Linux, so struct input_event is 16 bytes:
    struct timeval { uint32_t sec; uint32_t usec; }  // 8 bytes
    uint16_t type;
    uint16_t code;
    int32_t  value;
"""

import struct
from dataclasses import dataclass
from enum import IntEnum

# 32-bit ARM: struct input_event = 16 bytes
# 64-bit ARM: struct input_event = 24 bytes
EVENT_FORMAT_32 = "<IIHHi"  # 16 bytes
EVENT_FORMAT_64 = "<QQHHi"  # 24 bytes
EVENT_SIZE_32 = struct.calcsize(EVENT_FORMAT_32)  # 16
EVENT_SIZE_64 = struct.calcsize(EVENT_FORMAT_64)  # 24


class EventType(IntEnum):
    EV_SYN = 0x00
    EV_KEY = 0x01
    EV_REL = 0x02
    EV_ABS = 0x03


class AbsCode(IntEnum):
    ABS_X = 0x00
    ABS_Y = 0x01
    ABS_PRESSURE = 0x18
    ABS_DISTANCE = 0x19
    ABS_TILT_X = 0x1A
    ABS_TILT_Y = 0x1B


class KeyCode(IntEnum):
    BTN_TOUCH = 0x14A
    BTN_STYLUS = 0x14B
    BTN_STYLUS2 = 0x14C
    BTN_TOOL_PEN = 0x140
    BTN_TOOL_RUBBER = 0x141


class SynCode(IntEnum):
    SYN_REPORT = 0x00


# Control message type (type=0xFF, never produced by kernel)
EV_CONTROL = 0xFF

class ControlCode(IntEnum):
    CTRL_ROTATION   = 0x01
    CTRL_DISCONNECT = 0x02
    CTRL_SHORTCUT   = 0x03   # value = one of the SHORTCUT_* constants below


# Shortcut IDs (sent as CTRL_SHORTCUT value from the Kindle UI and forwarded
# to the platform input backend's send_shortcut() method).
SHORTCUT_UNDO          = 1   # Ctrl+Z  / Cmd+Z
SHORTCUT_REDO          = 2   # Ctrl+Y  / Cmd+Shift+Z
SHORTCUT_BRUSH_SMALLER = 3   # [
SHORTCUT_BRUSH_BIGGER  = 4   # ]
SHORTCUT_SAVE          = 5   # Ctrl+S  / Cmd+S
SHORTCUT_SLASH         = 6   # /  (brush cycle / user-defined)


@dataclass
class PenState:
    """Current state of the pen digitizer."""
    x: int = 0
    y: int = 0
    pressure: int = 0
    tilt_x: int = 0
    tilt_y: int = 0
    distance: int = 0
    in_range: bool = False  # pen is hovering near the surface
    touching: bool = False  # pen is touching the surface
    button1: bool = False   # side button (stylus button)
    button2: bool = False   # second side button
    eraser: bool = False    # eraser tool active
    dirty: bool = False     # has state changed since last sync?


class EventParser:
    """Parses raw Linux input events and maintains pen/touch state."""

    def __init__(self, arch_bits: int = 32):
        if arch_bits == 32:
            self._fmt = EVENT_FORMAT_32
            self._size = EVENT_SIZE_32
        else:
            self._fmt = EVENT_FORMAT_64
            self._size = EVENT_SIZE_64

        self.pen = PenState()
        self._buffer = bytearray()

    @property
    def event_size(self) -> int:
        return self._size

    def feed(self, data: bytes) -> list[str | tuple]:
        """Feed raw bytes and return list of completed event types.

        Returns a list of event names when a SYN_REPORT is received:
        - "pen" if pen state changed
        - ("control", code, value) for control messages
        """
        self._buffer.extend(data)
        results = []

        while len(self._buffer) >= self._size:
            raw = bytes(self._buffer[: self._size])
            del self._buffer[: self._size]

            parts = struct.unpack(self._fmt, raw)
            # parts: (tv_sec, tv_usec, type, code, value)
            ev_type = parts[2]
            ev_code = parts[3]
            ev_value = parts[4]

            result = self._process_event(ev_type, ev_code, ev_value)
            if result:
                results.append(result)

        return results

    def _process_event(self, ev_type: int, ev_code: int, ev_value: int) -> str | tuple | None:
        # Control messages (type 0xFF)
        if ev_type == EV_CONTROL:
            return ("control", ev_code, ev_value)

        if ev_type == EventType.EV_SYN:
            if ev_code == SynCode.SYN_REPORT:
                if self.pen.dirty:
                    self.pen.dirty = False
                    return "pen"
            return None

        if ev_type == EventType.EV_ABS:
            self._process_abs(ev_code, ev_value)
        elif ev_type == EventType.EV_KEY:
            self._process_key(ev_code, ev_value)

        return None

    def _process_abs(self, code: int, value: int) -> None:
        # Pen absolute axes
        if code == AbsCode.ABS_X:
            self.pen.x = value
            self.pen.dirty = True
        elif code == AbsCode.ABS_Y:
            self.pen.y = value
            self.pen.dirty = True
        elif code == AbsCode.ABS_PRESSURE:
            self.pen.pressure = value
            self.pen.dirty = True
        elif code == AbsCode.ABS_TILT_X:
            self.pen.tilt_x = value
            self.pen.dirty = True
        elif code == AbsCode.ABS_TILT_Y:
            self.pen.tilt_y = value
            self.pen.dirty = True
        elif code == AbsCode.ABS_DISTANCE:
            self.pen.distance = value
            self.pen.dirty = True

    def _process_key(self, code: int, value: int) -> None:
        pressed = value != 0
        if code == KeyCode.BTN_TOUCH:
            self.pen.touching = pressed
            self.pen.dirty = True
        elif code == KeyCode.BTN_TOOL_PEN:
            self.pen.in_range = pressed
            self.pen.eraser = False
            self.pen.dirty = True
        elif code == KeyCode.BTN_TOOL_RUBBER:
            self.pen.in_range = pressed
            self.pen.eraser = pressed
            self.pen.dirty = True
        elif code == KeyCode.BTN_STYLUS:
            self.pen.button1 = pressed
            self.pen.dirty = True
        elif code == KeyCode.BTN_STYLUS2:
            self.pen.button2 = pressed
            self.pen.dirty = True
