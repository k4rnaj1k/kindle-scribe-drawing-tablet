from __future__ import annotations

"""macOS input injection using CoreGraphics via ctypes.

Why ctypes instead of pyobjc:
  pyobjc's CGEventSetIntegerValueField / CGEventSetDoubleValueField silently
  write nothing -- every field reads back as 0.  ctypes calls the same C
  symbols directly and works correctly.

CGEventField values verified against fake_tablet.c v3 (which was tested
against CGEventTypes.h in the macOS SDK):

  Mouse event fields:
    kCGMouseEventNumber                 =  0
    kCGMouseEventClickState             =  1
    kCGMouseEventPressure               =  2   (double)
    kCGMouseEventButtonNumber           =  3
    kCGMouseEventDeltaX                 =  4
    kCGMouseEventDeltaY                 =  5
    kCGMouseEventSubtype                =  7   (0=default, 1=tablet point, 2=tablet proximity)

  Tablet point fields:
    kCGTabletEventPointX                = 15
    kCGTabletEventPointY                = 16
    kCGTabletEventPointZ                = 17
    kCGTabletEventPointButtons          = 18
    kCGTabletEventPointPressure         = 19   (double 0.0-1.0)
    kCGTabletEventTiltX                 = 20   (double)
    kCGTabletEventTiltY                 = 21   (double)
    kCGTabletEventRotation              = 22   (double)
    kCGTabletEventTangentialPressure    = 23   (double)
    kCGTabletEventDeviceID              = 24

  Tablet proximity fields:
    kCGTabletProximityEventVendorID               = 28
    kCGTabletProximityEventTabletID               = 29
    kCGTabletProximityEventPointerID              = 30
    kCGTabletProximityEventDeviceID               = 31
    kCGTabletProximityEventSystemTabletID         = 32
    kCGTabletProximityEventVendorPointerType      = 33
    kCGTabletProximityEventVendorPointerSerialNumber = 34
    kCGTabletProximityEventVendorUniqueID         = 35
    kCGTabletProximityEventCapabilityMask         = 36
    kCGTabletProximityEventPointerType            = 37
    kCGTabletProximityEventEnterProximity         = 38
"""

import ctypes
import ctypes.util
import logging
import sys

log = logging.getLogger(__name__)

# -- Load CoreGraphics ---------------------------------------------------------
_cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
_as = ctypes.CDLL(ctypes.util.find_library("ApplicationServices"))


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


# CGEventSourceCreate
_cg.CGEventSourceCreate.restype  = ctypes.c_void_p
_cg.CGEventSourceCreate.argtypes = [ctypes.c_int32]
# CGEventCreateMouseEvent
_cg.CGEventCreateMouseEvent.restype  = ctypes.c_void_p
_cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                         _CGPoint, ctypes.c_uint32]
# CGEventCreate (null event, for proximity)
_cg.CGEventCreate.restype  = ctypes.c_void_p
_cg.CGEventCreate.argtypes = [ctypes.c_void_p]
# CGEventSetType
_cg.CGEventSetType.restype  = None
_cg.CGEventSetType.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
# CGEventSetLocation
_cg.CGEventSetLocation.restype  = None
_cg.CGEventSetLocation.argtypes = [ctypes.c_void_p, _CGPoint]
# Field set/get
_cg.CGEventSetIntegerValueField.restype  = None
_cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
_cg.CGEventSetDoubleValueField.restype   = None
_cg.CGEventSetDoubleValueField.argtypes  = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_double]
# CGEventPost
_cg.CGEventPost.restype  = None
_cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
# CFRelease
_cg.CFRelease.restype  = None
_cg.CFRelease.argtypes = [ctypes.c_void_p]
# CGEventCreateScrollWheelEvent
_cg.CGEventCreateScrollWheelEvent.restype  = ctypes.c_void_p
_cg.CGEventCreateScrollWheelEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                               ctypes.c_uint32, ctypes.c_int32,
                                               ctypes.c_int32]
# CGEventCreateKeyboardEvent
_cg.CGEventCreateKeyboardEvent.restype  = ctypes.c_void_p
_cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16,
                                            ctypes.c_bool]
# CGEventSetFlags
_cg.CGEventSetFlags.restype  = None
_cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
# Display info
from Quartz import CGMainDisplayID, CGDisplayPixelsWide, CGDisplayPixelsHigh

# AXIsProcessTrusted
_as.AXIsProcessTrusted.restype  = ctypes.c_bool
_as.AXIsProcessTrusted.argtypes = []

# -- Constants -----------------------------------------------------------------

# CGEventTapLocation
_kCGHIDEventTap = 0

# CGEventType
_kCGEventMouseMoved        = 5
_kCGEventLeftMouseDown     = 1
_kCGEventLeftMouseUp       = 2
_kCGEventRightMouseDown    = 3
_kCGEventRightMouseUp      = 4
_kCGEventLeftMouseDragged  = 6
_kCGEventRightMouseDragged = 7
_kCGScrollWheel            = 22

# Native tablet event types (from IOLLEvent.h: NX_TABLETPOINTER=23, NX_TABLETPROXIMITY=24)
_kCGEventTabletPointer     = 23
_kCGEventTabletProximity   = 24   # was WRONG (23) in previous version!

# CGMouseButton
_kCGMouseButtonLeft  = 0
_kCGMouseButtonRight = 1

# CGEventSourceStateID
_kCGEventSourceStateHIDSystemState = 1

# CGScrollEventUnit
_kCGScrollEventUnitPixel = 1

# -- CGEventField values (verified against CGEventTypes.h / fake_tablet.c v3) --

# Mouse event fields
_F_MOUSE_PRESSURE    =  2   # kCGMouseEventPressure (double)
_F_MOUSE_DELTA_X     =  4   # kCGMouseEventDeltaX (integer + double)
_F_MOUSE_DELTA_Y     =  5   # kCGMouseEventDeltaY (integer + double)
_F_MOUSE_SUBTYPE     =  7   # kCGMouseEventSubtype

# Tablet point fields
_F_TAB_POINT_X       = 15   # kCGTabletEventPointX
_F_TAB_POINT_Y       = 16   # kCGTabletEventPointY
_F_TAB_PRESSURE      = 19   # kCGTabletEventPointPressure (double 0.0-1.0)
_F_TAB_TILT_X        = 20   # kCGTabletEventTiltX (double)
_F_TAB_TILT_Y        = 21   # kCGTabletEventTiltY (double)
_F_TAB_DEVICE_ID     = 24   # kCGTabletEventDeviceID

# Tablet proximity fields
_F_PROX_VENDOR_ID          = 28   # kCGTabletProximityEventVendorID
_F_PROX_TABLET_ID          = 29   # kCGTabletProximityEventTabletID
_F_PROX_POINTER_ID         = 30   # kCGTabletProximityEventPointerID
_F_PROX_DEVICE_ID          = 31   # kCGTabletProximityEventDeviceID
_F_PROX_SYSTEM_TABLET_ID   = 32   # kCGTabletProximityEventSystemTabletID
_F_PROX_VENDOR_PTR_TYPE    = 33   # kCGTabletProximityEventVendorPointerType
_F_PROX_VENDOR_PTR_SN      = 34   # kCGTabletProximityEventVendorPointerSerialNumber
_F_PROX_VENDOR_UNIQUE_ID   = 35   # kCGTabletProximityEventVendorUniqueID
_F_PROX_CAPABILITY_MASK    = 36   # kCGTabletProximityEventCapabilityMask
_F_PROX_POINTER_TYPE       = 37   # kCGTabletProximityEventPointerType
_F_PROX_ENTER              = 38   # kCGTabletProximityEventEnterProximity

# Mouse subtype constants
_SUBTYPE_DEFAULT            = 0
_SUBTYPE_TABLET_POINT       = 1
_SUBTYPE_TABLET_PROXIMITY   = 2

# Virtual key codes (from Carbon HIToolbox/Events.h)
_kVK_ANSI_S            = 0x01
_kVK_ANSI_Z            = 0x06
_kVK_ANSI_Y            = 0x10   # not standard for redo on macOS, but some apps use it
_kVK_ANSI_LeftBracket  = 0x21   # [
_kVK_ANSI_RightBracket = 0x1E   # ]
_kVK_ANSI_Slash        = 0x2C   # /
_kVK_Command           = 0x37   # left Command
_kVK_Shift             = 0x38   # left Shift

# CGEventFlags modifier masks
_kCGEventFlagMaskCommand = 0x00100000   # 1 << 20
_kCGEventFlagMaskShift   = 0x00020000   # 1 << 17

# NSPointingDeviceType (what Qt reads to determine pen vs eraser)
_NS_PEN_POINTING_DEVICE     = 1
_NS_CURSOR_POINTING_DEVICE  = 2
_NS_ERASER_POINTING_DEVICE  = 3

# Device identity — pen and eraser get separate IDs so apps (Qt/Krita) can
# distinguish them via cached proximity data, just like a real Wacom tablet.
_WACOM_VENDOR_ID   = 0x056A
_DEVICE_ID_PEN     = 1
_DEVICE_ID_ERASER  = 2
_POINTER_ID_PEN    = 1
_POINTER_ID_ERASER = 2
_UNIQUE_ID_PEN     = 0x0000000012345678
_UNIQUE_ID_ERASER  = 0x0000000087654321
_CAPABILITY_MASK   = 0x00FE


# -- Permission check ----------------------------------------------------------

def require_accessibility() -> None:
    if _as.AXIsProcessTrusted():
        return
    print(
        "\n"
        "  ERROR: Accessibility permission required for pressure sensitivity.\n"
        "\n"
        "  macOS strips tablet pressure data from injected events unless the\n"
        "  injecting process has Accessibility access.\n"
        "\n"
        "  Fix:\n"
        "    System Settings -> Privacy & Security -> Accessibility\n"
        "    -> click + and add Terminal (or your terminal app)\n"
        "    -> re-run kindle-tablet\n"
        "\n"
        "  If it's already listed, remove and re-add it.\n",
        file=sys.stderr,
    )
    sys.exit(1)


# -- MacOSInput ----------------------------------------------------------------

class MacOSInput:
    """Injects pen/tablet events on macOS with correct pressure for Krita.

    Proximity events are sent as native kCGEventTabletProximity (type 24).
    Qt's qnsview.mm tabletProximity: handler reads uniqueID, pointingDeviceType,
    capabilityMask, isEnteringProximity, and deviceID from the proximity event
    and stores them in tabletDeviceDataHash[deviceID].  If proximity doesn't
    register, Qt discards ALL subsequent tablet point events.

    Pen and eraser are given distinct device/pointer/unique IDs so Qt caches
    them as separate tools and reports the correct QTabletEvent::PointerType.
    """

    def __init__(self):
        require_accessibility()

        display_id = CGMainDisplayID()
        self.screen_width  = CGDisplayPixelsWide(display_id)
        self.screen_height = CGDisplayPixelsHigh(display_id)

        self._src = _cg.CGEventSourceCreate(_kCGEventSourceStateHIDSystemState)
        self._left_down    = False
        self._right_down   = False
        self._in_proximity = False
        self._is_eraser    = False
        # Track previous position to compute deltas for injected events.
        # Apps like Unity/Krita rely on kCGMouseEventDeltaX/Y for drag direction.
        self._prev_x: float = 0.0
        self._prev_y: float = 0.0

        log.info("macOS screen: %dx%d", self.screen_width, self.screen_height)

    # -- Internal helpers ------------------------------------------------------

    def _pt(self, x: float, y: float) -> _CGPoint:
        return _CGPoint(x, y)

    def _post(self, ev: ctypes.c_void_p) -> None:
        _cg.CGEventPost(_kCGHIDEventTap, ev)
        _cg.CFRelease(ev)

    def _mouse_event(self, ev_type: int, x: float, y: float,
                     button: int = _kCGMouseButtonLeft) -> ctypes.c_void_p:
        return _cg.CGEventCreateMouseEvent(self._src, ev_type, self._pt(x, y), button)

    def _set_deltas(self, ev, dx: float, dy: float) -> None:
        """Set mouse delta fields explicitly.

        Without this, macOS computes deltas from the last *real* mouse event,
        which can produce a large spurious delta that drawing apps misinterpret
        as a brushstroke (the brief up/down flick seen on macOS).
        Both integer and double fields must be set — different apps read both.
        """
        idx = int(round(dx))
        idy = int(round(dy))
        _cg.CGEventSetIntegerValueField(ev, _F_MOUSE_DELTA_X, idx)
        _cg.CGEventSetIntegerValueField(ev, _F_MOUSE_DELTA_Y, idy)
        _cg.CGEventSetDoubleValueField (ev, _F_MOUSE_DELTA_X, dx)
        _cg.CGEventSetDoubleValueField (ev, _F_MOUSE_DELTA_Y, dy)

    def _stamp_tablet(self, ev, pressure: float,
                      tilt_x: float, tilt_y: float,
                      eraser: bool = False) -> None:
        """Attach tablet fields so Qt/Krita creates a QTabletEvent."""
        device_id = _DEVICE_ID_ERASER if eraser else _DEVICE_ID_PEN
        _cg.CGEventSetIntegerValueField(ev, _F_MOUSE_SUBTYPE,  _SUBTYPE_TABLET_POINT)
        _cg.CGEventSetIntegerValueField(ev, _F_TAB_DEVICE_ID,  device_id)
        _cg.CGEventSetDoubleValueField (ev, _F_MOUSE_PRESSURE, pressure)
        _cg.CGEventSetDoubleValueField (ev, _F_TAB_PRESSURE,   pressure)
        _cg.CGEventSetDoubleValueField (ev, _F_TAB_TILT_X,     tilt_x)
        _cg.CGEventSetDoubleValueField (ev, _F_TAB_TILT_Y,     tilt_y)

    # -- Proximity -------------------------------------------------------------

    def _fill_proximity_fields(self, ev, entering: bool, eraser: bool) -> None:
        """Fill ALL proximity fields on a CGEvent, matching fake_tablet.c v3.

        Pen and eraser get distinct device/pointer/unique IDs so that Qt caches
        them as separate tools (keyed by deviceID) and reports the correct
        QTabletEvent::PointerType.  Using the same ID for both means Qt sees only
        the first-registered type and ignores subsequent proximity type changes.
        """
        ptr_type  = _NS_ERASER_POINTING_DEVICE if eraser else _NS_PEN_POINTING_DEVICE
        device_id = _DEVICE_ID_ERASER          if eraser else _DEVICE_ID_PEN
        ptr_id    = _POINTER_ID_ERASER         if eraser else _POINTER_ID_PEN
        unique_id = _UNIQUE_ID_ERASER          if eraser else _UNIQUE_ID_PEN
        _cg.CGEventSetIntegerValueField(ev, _F_PROX_VENDOR_ID,        _WACOM_VENDOR_ID)
        _cg.CGEventSetIntegerValueField(ev, _F_PROX_TABLET_ID,        1)
        _cg.CGEventSetIntegerValueField(ev, _F_PROX_POINTER_ID,       ptr_id)
        _cg.CGEventSetIntegerValueField(ev, _F_PROX_DEVICE_ID,        device_id)
        _cg.CGEventSetIntegerValueField(ev, _F_PROX_SYSTEM_TABLET_ID, 1)
        _cg.CGEventSetIntegerValueField(ev, _F_PROX_VENDOR_PTR_TYPE,  ptr_type)
        _cg.CGEventSetIntegerValueField(ev, _F_PROX_POINTER_TYPE,     ptr_type)
        _cg.CGEventSetIntegerValueField(ev, _F_PROX_ENTER,            1 if entering else 0)
        _cg.CGEventSetIntegerValueField(ev, _F_PROX_VENDOR_UNIQUE_ID, unique_id)
        _cg.CGEventSetIntegerValueField(ev, _F_PROX_CAPABILITY_MASK,  _CAPABILITY_MASK)

    def _send_proximity(self, x: float, y: float,
                        enter: bool, eraser: bool = False) -> None:
        """Send a native kCGEventTabletProximity event (type 24).

        We intentionally send only the native event and skip the old
        "Method 2" MouseMoved-with-proximity-subtype backup.  That backup
        injected an extra kCGEventMouseMoved at the current position which
        drawing apps received as a real cursor move — causing the brief
        phantom brushstroke (up/down flick) reported on macOS.  Modern Qt
        versions (5.x+) handle the native proximity event correctly.
        """
        pt = self._pt(x, y)
        ev = _cg.CGEventCreate(self._src)
        if ev:
            _cg.CGEventSetType(ev, _kCGEventTabletProximity)
            _cg.CGEventSetLocation(ev, pt)
            self._fill_proximity_fields(ev, enter, eraser)
            _cg.CGEventPost(_kCGHIDEventTap, ev)
            _cg.CFRelease(ev)

    def _ensure_proximity(self, x: float, y: float, eraser: bool) -> None:
        if not self._in_proximity:
            self._send_proximity(x, y, enter=True, eraser=eraser)
            self._in_proximity = True
            self._is_eraser    = eraser
        elif eraser != self._is_eraser:
            # Tool switched (pen ↔ eraser): tell the app the old tool left
            # before the new one enters.  Without the leave event Qt keeps the
            # stale pointer-type in its device cache and treats eraser strokes
            # as normal pen strokes.
            self._send_proximity(x, y, enter=False, eraser=self._is_eraser)
            self._send_proximity(x, y, enter=True,  eraser=eraser)
            self._is_eraser = eraser

    def _leave_proximity(self, x: float, y: float) -> None:
        if self._in_proximity:
            self._send_proximity(x, y, enter=False, eraser=self._is_eraser)
            self._in_proximity = False

    # -- Public API ------------------------------------------------------------

    def move(self, x: float, y: float, pressure: float = 0.0,
             tilt_x: float = 0.0, tilt_y: float = 0.0,
             eraser: bool = False) -> None:
        self._ensure_proximity(x, y, eraser)
        if self._left_down:
            ev_type, btn = _kCGEventLeftMouseDragged,  _kCGMouseButtonLeft
        elif self._right_down:
            ev_type, btn = _kCGEventRightMouseDragged, _kCGMouseButtonRight
        else:
            ev_type, btn = _kCGEventMouseMoved,        _kCGMouseButtonLeft
        ev = self._mouse_event(ev_type, x, y, btn)
        self._set_deltas(ev, x - self._prev_x, y - self._prev_y)
        self._stamp_tablet(ev, pressure, tilt_x, tilt_y, eraser)
        self._post(ev)
        self._prev_x, self._prev_y = x, y

    def pen_down(self, x: float, y: float, pressure: float = 0.5,
                 tilt_x: float = 0.0, tilt_y: float = 0.0,
                 eraser: bool = False) -> None:
        self._ensure_proximity(x, y, eraser)
        ev = self._mouse_event(_kCGEventLeftMouseDown, x, y)
        self._set_deltas(ev, x - self._prev_x, y - self._prev_y)
        self._stamp_tablet(ev, pressure, tilt_x, tilt_y, eraser)
        self._post(ev)
        self._left_down = True
        self._prev_x, self._prev_y = x, y

    def pen_up(self, x: float, y: float) -> None:
        ev = self._mouse_event(_kCGEventLeftMouseUp, x, y)
        self._set_deltas(ev, x - self._prev_x, y - self._prev_y)
        self._stamp_tablet(ev, 0.0, 0.0, 0.0, self._is_eraser)
        self._post(ev)
        self._left_down = False
        self._prev_x, self._prev_y = x, y

    def pen_leave(self, x: float, y: float) -> None:
        if self._left_down:
            self.pen_up(x, y)
        self._leave_proximity(x, y)

    def button_down(self, x: float, y: float) -> None:
        ev = self._mouse_event(_kCGEventRightMouseDown, x, y, _kCGMouseButtonRight)
        self._post(ev)
        self._right_down = True

    def button_up(self, x: float, y: float) -> None:
        ev = self._mouse_event(_kCGEventRightMouseUp, x, y, _kCGMouseButtonRight)
        self._post(ev)
        self._right_down = False

    def scroll(self, dx: int, dy: int) -> None:
        ev = _cg.CGEventCreateScrollWheelEvent(
            self._src, _kCGScrollEventUnitPixel, 2,
            ctypes.c_int32(dy), ctypes.c_int32(dx)
        )
        self._post(ev)

    # -- Keyboard shortcuts ----------------------------------------------------

    def _send_key(self, keycode: int, flags: int = 0) -> None:
        """Inject a key combo with correct modifier press/release sequencing.

        Each modifier is sent as its own physical key-down event before the
        main key, and as a key-up after — with the accumulated flag mask kept
        consistent on every event.  This is what Krita (and other Qt apps)
        expect: they track modifier state from the physical key events, not
        just from the flags field.  Without this the Command key can appear
        to stay held after a Cmd+Z.

        A 10 ms hold between the last key-down and the first key-up gives the
        receiving app enough time to process the combo.
        """
        # Map flag bits → (virtual keycode, flag bit) in press order
        mod_map = []
        if flags & _kCGEventFlagMaskCommand:
            mod_map.append((_kVK_Command, _kCGEventFlagMaskCommand))
        if flags & _kCGEventFlagMaskShift:
            mod_map.append((_kVK_Shift,   _kCGEventFlagMaskShift))

        active_flags = 0

        def _post_key(vk: int, down: bool, f: int) -> None:
            ev = _cg.CGEventCreateKeyboardEvent(self._src, vk, down)
            if ev:
                _cg.CGEventSetFlags(ev, f)
                _cg.CGEventPost(_kCGHIDEventTap, ev)
                _cg.CFRelease(ev)

        # Press modifiers
        for mod_vk, mod_flag in mod_map:
            active_flags |= mod_flag
            _post_key(mod_vk, True, active_flags)

        # Key down
        _post_key(keycode, True, active_flags)

        time.sleep(0.010)   # 10 ms hold — long enough for Krita to register

        # Key up (flags still active until modifiers are released)
        _post_key(keycode, False, active_flags)

        # Release modifiers in reverse order
        for mod_vk, mod_flag in reversed(mod_map):
            active_flags &= ~mod_flag
            _post_key(mod_vk, False, active_flags)

    def send_shortcut(self, shortcut_id: int) -> None:
        """Inject a keyboard shortcut on macOS for the given shortcut_id.

        Shortcut IDs (from events.py):
          1 = Undo          Cmd+Z
          2 = Redo          Cmd+Shift+Z
          3 = Brush smaller [
          4 = Brush bigger  ]
          5 = Save          Cmd+S
          6 = Slash         /
        """
        from .events import (SHORTCUT_UNDO, SHORTCUT_REDO,
                             SHORTCUT_BRUSH_SMALLER, SHORTCUT_BRUSH_BIGGER,
                             SHORTCUT_SAVE, SHORTCUT_SLASH)
        if shortcut_id == SHORTCUT_UNDO:
            self._send_key(_kVK_ANSI_Z, _kCGEventFlagMaskCommand)
        elif shortcut_id == SHORTCUT_REDO:
            self._send_key(_kVK_ANSI_Z,
                           _kCGEventFlagMaskCommand | _kCGEventFlagMaskShift)
        elif shortcut_id == SHORTCUT_BRUSH_SMALLER:
            self._send_key(_kVK_ANSI_LeftBracket)
        elif shortcut_id == SHORTCUT_BRUSH_BIGGER:
            self._send_key(_kVK_ANSI_RightBracket)
        elif shortcut_id == SHORTCUT_SAVE:
            self._send_key(_kVK_ANSI_S, _kCGEventFlagMaskCommand)
        elif shortcut_id == SHORTCUT_SLASH:
            self._send_key(_kVK_ANSI_Slash)
        else:
            log.warning("send_shortcut: unknown shortcut_id %d", shortcut_id)
