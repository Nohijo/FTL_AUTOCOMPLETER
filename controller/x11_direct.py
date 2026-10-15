"""Send mouse and keyboard events straight to the FTL window with XSendEvent.

XTEST input (what xdotool normally uses) goes to whatever window has focus and
moves the real pointer. Events sent with XSendEvent go to one window only and
leave the pointer and focus alone, so the bot can play while you use other
windows. FTL's input layer (SIL) does not drop such synthetic events, and FTL
does not filter input by focus (checked in FTL.amd64 1.6.12: sys_input_update,
CApp::OnInputFocus/OnInputBlur), so they act like real ones.
"""

import ctypes
import ctypes.util
import time

KEY_PRESS, KEY_RELEASE, BUTTON_PRESS, BUTTON_RELEASE, MOTION_NOTIFY = 2, 3, 4, 5, 6


class _InputEvent(ctypes.Structure):
    """XKeyEvent / XButtonEvent / XMotionEvent share this layout; detail is the
    keycode, the button or is_hint."""
    _fields_ = [
        ("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong),
        ("x", ctypes.c_int), ("y", ctypes.c_int), ("x_root", ctypes.c_int), ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint), ("detail", ctypes.c_uint), ("same_screen", ctypes.c_int),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("input", _InputEvent), ("pad", ctypes.c_long * 24)]


_BUTTON_MASKS = {1: 1 << 8, 2: 1 << 9, 3: 1 << 10}
_MODIFIER_MASKS = {"Shift_L": 1, "Shift_R": 1, "Control_L": 4, "Control_R": 4, "Alt_L": 8, "Alt_R": 8}


class DirectInput:
    """One X connection that delivers events to a single window."""

    def __init__(self, display: str | None = None):
        self.xlib = ctypes.CDLL(ctypes.util.find_library("X11"))
        self.xlib.XOpenDisplay.restype = ctypes.c_void_p
        self.xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        self.xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.xlib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                                         ctypes.POINTER(_XEvent)]
        self.xlib.XFlush.argtypes = [ctypes.c_void_p]
        self.xlib.XStringToKeysym.restype = ctypes.c_ulong
        self.xlib.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.xlib.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.display = self.xlib.XOpenDisplay(display.encode() if display else None)
        if not self.display:
            raise RuntimeError("cannot open the X display")
        self.root = self.xlib.XDefaultRootWindow(self.display)
        self.pointer = (0, 0)
        self.buttons = 0
        self.modifiers = 0                 # held Shift/Ctrl/Alt, reported in every event's state
        self.last_key = (None, 0.0)

    def _send(self, window: int, kind: int, detail: int = 0):
        event = _XEvent()
        e = event.input
        e.type, e.send_event, e.display = kind, 1, self.display
        # Timestamp 0 (CurrentTime): SIL then stamps the event with its own clock. Any other
        # value is read as X server time (sys_input_update, handle_button_event,
        # handle_motion_event); one out of order with the server's makes SIL assume its 32-bit
        # clock wrapped and shift x11_timestamp_epoch by ~49.7 days, which put the bot's keys
        # (and later real input) at the wrong time.
        e.window, e.root, e.subwindow, e.time = window, self.root, 0, 0
        e.x, e.y = self.pointer
        e.x_root, e.y_root = self.pointer
        e.state, e.detail, e.same_screen = self.buttons | self.modifiers, detail, 1
        # An empty event mask delivers the event to the client that created the window: FTL.
        self.xlib.XSendEvent(self.display, window, 0, 0, ctypes.byref(event))
        self.xlib.XFlush(self.display)

    def move(self, window: int, x: int, y: int):
        self.pointer = (x, y)
        self._send(window, MOTION_NOTIFY)

    def button(self, window: int, button: int, pressed: bool):
        self._send(window, BUTTON_PRESS if pressed else BUTTON_RELEASE, button)
        mask = _BUTTON_MASKS.get(button, 0)
        self.buttons = self.buttons | mask if pressed else self.buttons & ~mask

    def key(self, window: int, keysym: str, pressed: bool) -> bool:
        code = self.xlib.XKeysymToKeycode(self.display, self.xlib.XStringToKeysym(keysym.encode()))
        if not code:
            return False
        if pressed and self.last_key[0] == code and time.monotonic() - self.last_key[1] < 0.05:
            # A release followed by a press of the same key with the same (zero) timestamp
            # looks like auto-repeat to SIL; leave a frame between two taps of one key.
            time.sleep(0.05)
        if not pressed:
            self.last_key = (code, time.monotonic())
        self._send(window, KEY_PRESS if pressed else KEY_RELEASE, code)
        mask = _MODIFIER_MASKS.get(keysym, 0)
        self.modifiers = self.modifiers | mask if pressed else self.modifiers & ~mask
        return True
