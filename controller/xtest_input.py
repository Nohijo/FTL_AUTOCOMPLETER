"""XTEST input through one persistent X connection (ctypes, libX11 + libXtst).

The same events xdotool sends, without starting an xdotool process for every move, click
and key (each one cost 10-30 ms). Used for FTL's own Xephyr display, where FTL is the
only window, so XTEST input can only ever reach FTL.
"""

import ctypes
import ctypes.util


class XTestInput:
    def __init__(self, display: str):
        x11 = ctypes.util.find_library("X11")
        xtst = ctypes.util.find_library("Xtst")
        if not x11 or not xtst:
            raise RuntimeError("libX11 / libXtst not found")
        self.xlib = ctypes.CDLL(x11)
        self.xtst = ctypes.CDLL(xtst)
        x, t = self.xlib, self.xtst
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                                            ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        t.XTestQueryExtension.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_int)] * 4
        t.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        t.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        t.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.display = x.XOpenDisplay(display.encode())
        if not self.display:
            raise RuntimeError(f"cannot open X display {display}")
        dummy = [ctypes.c_int() for _ in range(4)]
        if not t.XTestQueryExtension(self.display, *[ctypes.byref(d) for d in dummy]):
            raise RuntimeError(f"X display {display} has no XTEST extension")
        self.root = x.XDefaultRootWindow(self.display)
        self.keycodes: dict[str, int] = {}

    def _keycode(self, keysym: str) -> int:
        if keysym not in self.keycodes:
            code = self.xlib.XKeysymToKeycode(self.display, self.xlib.XStringToKeysym(keysym.encode()))
            if not code:
                raise ValueError(f"no keycode for keysym {keysym!r}")
            self.keycodes[keysym] = code
        return self.keycodes[keysym]

    def move(self, window: int, x: int, y: int):
        """Put the pointer on window pixel (x, y)."""
        rx, ry, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        self.xlib.XTranslateCoordinates(self.display, window, self.root, x, y,
                                        ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(child))
        self.xtst.XTestFakeMotionEvent(self.display, -1, rx.value, ry.value, 0)
        self.xlib.XSync(self.display, 0)

    def button(self, window: int, button: int, press: bool):
        self.xtst.XTestFakeButtonEvent(self.display, button, 1 if press else 0, 0)
        self.xlib.XSync(self.display, 0)

    def key(self, window: int, keysym: str, press: bool):
        self.xtst.XTestFakeKeyEvent(self.display, self._keycode(keysym), 1 if press else 0, 0)
        self.xlib.XSync(self.display, 0)
