"""Windows input injection using ctypes and SendInput.

Supports:
- Cursor movement
- Left/right click
- Pressure via pointer pen input (Windows Ink)
"""

import ctypes
import ctypes.wintypes
import logging
import sys

log = logging.getLogger(__name__)

if sys.platform != "win32":
    # Stub for import on non-Windows
    class WindowsInput:
        def __init__(self):
            raise RuntimeError("WindowsInput only works on Windows")
else:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # --- SendInput structures ---
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_VIRTUALDESK = 0x4000

    INPUT_MOUSE = 0

    # --- Pointer input (Windows 8+ Ink) ---
    POINTER_FLAG_INRANGE = 0x00000002
    POINTER_FLAG_INCONTACT = 0x00000004
    POINTER_FLAG_DOWN = 0x00010000
    POINTER_FLAG_UPDATE = 0x00020000
    POINTER_FLAG_UP = 0x00040000
    PT_PEN = 0x00000003

    PEN_FLAG_NONE = 0x00000000
    PEN_FLAG_BARREL = 0x00000001
    PEN_FLAG_INVERTED = 0x00000002
    PEN_FLAG_ERASER = 0x00000004
    PEN_MASK_PRESSURE = 0x00000001
    PEN_MASK_TILT_X = 0x00000004
    PEN_MASK_TILT_Y = 0x00000008

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.wintypes.LONG),
            ("dy", ctypes.wintypes.LONG),
            ("mouseData", ctypes.wintypes.DWORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        class _INPUT_UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT)]

        _anonymous_ = ("_input",)
        _fields_ = [
            ("type", ctypes.wintypes.DWORD),
            ("_input", _INPUT_UNION),
        ]

    class POINTER_INFO(ctypes.Structure):
        _fields_ = [
            ("pointerType", ctypes.c_uint32),
            ("pointerId", ctypes.c_uint32),
            ("frameId", ctypes.c_uint32),
            ("pointerFlags", ctypes.c_uint32),
            ("sourceDevice", ctypes.wintypes.HANDLE),
            ("hwndTarget", ctypes.wintypes.HWND),
            ("ptPixelLocation", ctypes.wintypes.POINT),
            ("ptHimetricLocation", ctypes.wintypes.POINT),
            ("ptPixelLocationRaw", ctypes.wintypes.POINT),
            ("ptHimetricLocationRaw", ctypes.wintypes.POINT),
            ("dwTime", ctypes.wintypes.DWORD),
            ("historyCount", ctypes.c_uint32),
            ("InputData", ctypes.c_int32),
            ("dwKeyStates", ctypes.wintypes.DWORD),
            ("PerformanceCount", ctypes.c_uint64),
            ("ButtonChangeType", ctypes.c_int32),
        ]

    class POINTER_PEN_INFO(ctypes.Structure):
        _fields_ = [
            ("pointerInfo", POINTER_INFO),
            ("penFlags", ctypes.c_uint32),
            ("penMask", ctypes.c_uint32),
            ("pressure", ctypes.c_uint32),
            ("rotation", ctypes.c_uint32),
            ("tiltX", ctypes.c_int32),
            ("tiltY", ctypes.c_int32),
        ]

    class WindowsInput:
        """Injects pen/mouse events on Windows."""

        def __init__(self):
            self.screen_width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            self.screen_height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
            self._left_down = False
            self._right_down = False
            # Check if InjectSyntheticPointerInput is available (Windows 10 1803+)
            try:
                self._create_pointer = user32.CreateSyntheticPointerDevice
                self._inject_pointer = user32.InjectSyntheticPointerInput
                self._has_pointer_api = True
                # Create a synthetic pen device with 1 pointer
                self._pen_device = self._create_pointer(PT_PEN, 1, 0)
                if self._pen_device:
                    log.info("Windows Ink pointer injection available")
                else:
                    self._has_pointer_api = False
            except AttributeError:
                self._has_pointer_api = False
                log.info("Windows Ink pointer injection not available, using SendInput")

            log.info("Windows screen: %dx%d", self.screen_width, self.screen_height)

        def _to_absolute(self, x: float, y: float) -> tuple[int, int]:
            """Convert screen coords to absolute coords (0-65535)."""
            abs_x = int(x / self.screen_width * 65535)
            abs_y = int(y / self.screen_height * 65535)
            return abs_x, abs_y

        def _send_mouse(self, x: float, y: float, flags: int) -> None:
            abs_x, abs_y = self._to_absolute(x, y)
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.mi.dx = abs_x
            inp.mi.dy = abs_y
            inp.mi.dwFlags = flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        def _send_pen_pointer(self, x: float, y: float, pressure: float,
                              tilt_x: float, tilt_y: float,
                              pointer_flags: int, pen_flags: int = 0) -> None:
            """Send pen input via InjectSyntheticPointerInput."""
            if not self._has_pointer_api:
                return

            pen_info = POINTER_PEN_INFO()
            pen_info.pointerInfo.pointerType = PT_PEN
            pen_info.pointerInfo.pointerId = 0
            pen_info.pointerInfo.pointerFlags = pointer_flags
            pen_info.pointerInfo.ptPixelLocation.x = int(x)
            pen_info.pointerInfo.ptPixelLocation.y = int(y)
            pen_info.penFlags = pen_flags
            pen_info.penMask = PEN_MASK_PRESSURE | PEN_MASK_TILT_X | PEN_MASK_TILT_Y
            pen_info.pressure = int(pressure * 1024)
            pen_info.tiltX = int(tilt_x * 90)
            pen_info.tiltY = int(tilt_y * 90)

            self._inject_pointer(self._pen_device, ctypes.byref(pen_info), 1)

        def move(self, x: float, y: float, pressure: float = 0.0,
                 tilt_x: float = 0.0, tilt_y: float = 0.0) -> None:
            """Move cursor to (x, y) in screen coordinates."""
            if self._has_pointer_api and self._left_down:
                flags = POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_UPDATE
                self._send_pen_pointer(x, y, pressure, tilt_x, tilt_y, flags)
            else:
                self._send_mouse(x, y, MOUSEEVENTF_MOVE)

        def pen_down(self, x: float, y: float, pressure: float = 0.5,
                     tilt_x: float = 0.0, tilt_y: float = 0.0) -> None:
            if self._has_pointer_api:
                flags = POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_DOWN
                self._send_pen_pointer(x, y, pressure, tilt_x, tilt_y, flags)
            else:
                self._send_mouse(x, y, MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTDOWN)
            self._left_down = True

        def pen_up(self, x: float, y: float) -> None:
            if self._has_pointer_api:
                flags = POINTER_FLAG_INRANGE | POINTER_FLAG_UP
                self._send_pen_pointer(x, y, 0, 0, 0, flags)
            else:
                self._send_mouse(x, y, MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTUP)
            self._left_down = False

        def button_down(self, x: float, y: float) -> None:
            self._send_mouse(x, y, MOUSEEVENTF_MOVE | MOUSEEVENTF_RIGHTDOWN)
            self._right_down = True

        def button_up(self, x: float, y: float) -> None:
            self._send_mouse(x, y, MOUSEEVENTF_MOVE | MOUSEEVENTF_RIGHTUP)
            self._right_down = False

        def scroll(self, dx: int, dy: int) -> None:
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.mi.mouseData = dy * 120  # WHEEL_DELTA
            inp.mi.dwFlags = 0x0800  # MOUSEEVENTF_WHEEL
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
