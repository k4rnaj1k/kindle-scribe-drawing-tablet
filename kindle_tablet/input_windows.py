"""Windows input injection using HID reports to XP-Pen VMulti driver.

Supports:
- Cursor movement
- Left/right click
- Pressure via pointer pen input (Windows Ink)
"""

import ctypes
import ctypes.wintypes
import logging
import struct
import sys

try:
    import hid
except ImportError:
    hid = None

log = logging.getLogger(__name__)

if sys.platform != "win32":
    class WindowsInput:
        def __init__(self):
            raise RuntimeError("WindowsInput only works on Windows")
else:
    user32 = ctypes.windll.user32

    # ---------- SendInput structures (keyboard) ----------
    _INPUT_KEYBOARD  = 1
    _KEYEVENTF_KEYUP = 0x0002

    # Virtual-key codes
    _VK_Z     = 0x5A
    _VK_S     = 0x53
    _VK_OEM_4 = 0xDB   # [ on US layout
    _VK_OEM_6 = 0xDD   # ] on US layout
    _VK_OEM_2 = 0xBF   # / on US layout
    _VK_SHIFT = 0x10
    _VK_CONTROL = 0x11

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk",         ctypes.wintypes.WORD),
            ("wScan",       ctypes.wintypes.WORD),
            ("dwFlags",     ctypes.wintypes.DWORD),
            ("time",        ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("_pad", ctypes.c_byte * 28)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.wintypes.DWORD), ("_input", _INPUT_UNION)]

    # Bit positions from C# BitPositions enum
    class BitPositions:
        Press = 0
        Barrel = 1
        Eraser = 2
        Invert = 3
        InRange = 4

    class WindowsInput:
        """Injects pen/mouse events on Windows using VMulti HID."""

        def __init__(self):
            self.screen_width = user32.GetSystemMetrics(0)
            self.screen_height = user32.GetSystemMetrics(1)
            self._left_down = False
            self._right_down = False
            self._eraser_active = False
            self._device = None

            if hid is None:
                log.error("The 'hid' module is required. Install with 'pip install hid'.")
                return

            # Find the XP-Pen VMulti device (ProductID 47820)
            # We try to find the specific interface that accepts our report.
            devices = hid.enumerate(0, 47820)
            if not devices:
                log.error("VMulti HID device not found. Ensure XP-Pen driver is installed.")
                return

            for dev_info in devices:
                try:
                    d = hid.device()
                    d.open_path(dev_info['path'])
                    # Test report to see if this is the correct collection (Digitizer)
                    # We send a dummy 65-byte report. If it returns -1, it's the wrong interface.
                    test_report = b'\x40' + (b'\x00' * 64)
                    if d.write(test_report) > 0:
                        self._device = d
                        log.info(f"Connected to VMulti Digitizer interface: {dev_info['path']}")
                        break
                    else:
                        d.close()
                except Exception:
                    continue

            if not self._device:
                log.error("Could not find a VMulti interface that accepts HID reports.")

        def _get_buttons(self) -> int:
            """Constructs the button state byte using C# BitPositions.

            Eraser end proximity sets Invert; eraser contact uses Eraser bit
            (not Press) so Windows Ink recognises it as the eraser tool.
            Regular tip contact uses Press as before.
            """
            buttons = (1 << BitPositions.InRange)
            if self._eraser_active:
                buttons |= (1 << BitPositions.Invert)
                if self._left_down:
                    buttons |= (1 << BitPositions.Eraser)
            else:
                if self._left_down:
                    buttons |= (1 << BitPositions.Press)
            if self._right_down:
                buttons |= (1 << BitPositions.Barrel)
            return buttons

        def _send_report(self, x: float, y: float, pressure: float, buttons: int) -> None:
            """Packs and writes the 65-byte HID packet."""
            if not self._device:
                return

            # Map coordinates (0-32767) and pressure (0-8191) per C# logic
            abs_x = max(0, min(32767, int((x / self.screen_width) * 32767)))
            abs_y = max(0, min(32767, int((y / self.screen_height) * 32767)))
            abs_pressure = max(0, min(8191, int(pressure * 8191)))

            # 10-byte struct from C#
            # <BBBBHHH = LittleEndian, 4xBytes, 3xUnsignedShorts
            payload = struct.pack('<BBBBHHH', 
                                  0x40,         # vmultiId (Report ID)
                                  0x0A,         # reportLength
                                  0x05,         # reportId
                                  buttons, 
                                  abs_x, 
                                  abs_y, 
                                  abs_pressure)

            # Pad to 65 bytes total. 
            # In hidapi, if buffer[0] is 0x40, it is treated as the Report ID.
            full_report = payload + (b'\x00' * (65 - len(payload)))

            try:
                res = self._device.write(full_report)
                if res < 0:
                    log.error("HID write failed.")
            except Exception as e:
                log.error(f"HID communication error: {e}")

        def move(self, x: float, y: float, pressure: float = 0.0,
                 tilt_x: float = 0.0, tilt_y: float = 0.0,
                 eraser: bool = False) -> None:
            """Move cursor to (x, y)."""
            self._eraser_active = eraser
            self._send_report(x, y, pressure, self._get_buttons())

        def pen_down(self, x: float, y: float, pressure: float = 0.5,
                     tilt_x: float = 0.0, tilt_y: float = 0.0,
                     eraser: bool = False) -> None:
            """Pen contact initiated."""
            self._eraser_active = eraser
            self._left_down = True
            self._send_report(x, y, pressure, self._get_buttons())

        def pen_up(self, x: float, y: float) -> None:
            """Pen contact lifted."""
            self._left_down = False
            self._send_report(x, y, 0.0, self._get_buttons())
            self._eraser_active = False

        def button_down(self, x: float, y: float) -> None:
            """Right click / Barrel button down."""
            self._right_down = True
            self._send_report(x, y, 0.0, self._get_buttons())

        def button_up(self, x: float, y: float) -> None:
            """Right click / Barrel button up."""
            self._right_down = False
            self._send_report(x, y, 0.0, self._get_buttons())

        def scroll(self, dx: int, dy: int) -> None:
            """Scroll fallback (Not supported via VMulti InkReport)."""
            pass

        # -- Keyboard shortcuts ------------------------------------------------

        def _send_key_combo(self, vk: int, ctrl: bool = False,
                            shift: bool = False) -> None:
            """Press modifier(s) + vk, then release in reverse order."""
            seq = []
            if ctrl:
                seq.append(_VK_CONTROL)
            if shift:
                seq.append(_VK_SHIFT)
            seq.append(vk)

            # Build key-down + key-up sequence
            events = [(k, False) for k in seq] + [(k, True) for k in reversed(seq)]
            inputs = (_INPUT * len(events))()
            for i, (key, up) in enumerate(events):
                inputs[i].type = _INPUT_KEYBOARD
                inputs[i]._input.ki.wVk = key
                inputs[i]._input.ki.dwFlags = _KEYEVENTF_KEYUP if up else 0

            user32.SendInput(len(events), inputs, ctypes.sizeof(_INPUT))

        def send_shortcut(self, shortcut_id: int) -> None:
            """Inject a keyboard shortcut on Windows for the given shortcut_id.

            Shortcut IDs (from events.py):
              1 = Undo          Ctrl+Z
              2 = Redo          Ctrl+Y
              3 = Brush smaller [
              4 = Brush bigger  ]
              5 = Save          Ctrl+S
              6 = Slash         /
            """
            from .events import (SHORTCUT_UNDO, SHORTCUT_REDO,
                                 SHORTCUT_BRUSH_SMALLER, SHORTCUT_BRUSH_BIGGER,
                                 SHORTCUT_SAVE, SHORTCUT_SLASH)
            if shortcut_id == SHORTCUT_UNDO:
                self._send_key_combo(_VK_Z, ctrl=True)
            elif shortcut_id == SHORTCUT_REDO:
                self._send_key_combo(_VK_Z, ctrl=True, shift=True)
            elif shortcut_id == SHORTCUT_BRUSH_SMALLER:
                self._send_key_combo(_VK_OEM_4)
            elif shortcut_id == SHORTCUT_BRUSH_BIGGER:
                self._send_key_combo(_VK_OEM_6)
            elif shortcut_id == SHORTCUT_SAVE:
                self._send_key_combo(_VK_S, ctrl=True)
            elif shortcut_id == SHORTCUT_SLASH:
                self._send_key_combo(_VK_OEM_2)