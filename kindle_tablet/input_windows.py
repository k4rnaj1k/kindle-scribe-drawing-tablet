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
            """Constructs the button state byte using C# BitPositions."""
            buttons = (1 << BitPositions.InRange)
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

        # --- RESTORED API SIGNATURES ---

        def move(self, x: float, y: float, pressure: float = 0.0,
                 tilt_x: float = 0.0, tilt_y: float = 0.0) -> None:
            """Move cursor to (x, y). Corrected to accept all positional args."""
            self._send_report(x, y, pressure, self._get_buttons())

        def pen_down(self, x: float, y: float, pressure: float = 0.5,
                     tilt_x: float = 0.0, tilt_y: float = 0.0) -> None:
            """Pen contact initiated."""
            self._left_down = True
            self._send_report(x, y, pressure, self._get_buttons())

        def pen_up(self, x: float, y: float) -> None:
            """Pen contact lifted."""
            self._left_down = False
            self._send_report(x, y, 0.0, self._get_buttons())

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