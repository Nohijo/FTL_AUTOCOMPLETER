"""The bot's status drawn over FTL, in the black bars above and below the game.

    python3 overlay.py            start it in the background (ftl-bot does this)
    python3 overlay.py --stop     stop it

It shows only while FTL is the active window, ignores the mouse (clicks go
through to the game) and never takes focus. Fullscreen FTL grabs the pointer,
so the overlay's controls are keys instead of buttons. The top bar shows only
those switches: F9 turns auto-play on/off (off = manual control), F10 / F11
lower / raise the game speed. The bottom bar shows the bot's latest lines. Without black bars (a
windowed or unstretched FTL) it uses a translucent box in FTL's top-right
corner instead. The dashboard's "Overlay on game" switch hides it.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import game_display
from config import LOG_DIR, PROJECT_DIR

PID_FILE = PROJECT_DIR / "data" / "overlay.pid"
OVERLAY_LOG = LOG_DIR / "overlay.log"
LOG_LINES = 3
COLORS = [  # first match wins; same meaning as the dashboard's log colours
    (("failed", "not found", "did not", "not executed", "could not", "stuck"), (0.97, 0.32, 0.29)),
    (("COMBAT", "target:", "ESCAPE", "power_weapon"), (0.97, 0.45, 0.4)),
    (("EVENT", "event:"), (0.35, 0.65, 1.0)),
    (("JUMP", "jump:", "next sector"), (0.25, 0.73, 0.31)),
    (("STORE", "store:"), (0.82, 0.6, 0.13)),
    (("UPGRADE", "upgrade:"), (0.74, 0.55, 1.0)),
    (("door:",), (1.0, 0.62, 0.39)),
    (("crew_to_room", "medbay", "station"), (0.22, 0.77, 0.81)),
]


def line_color(line: str) -> tuple[float, float, float]:
    for words, color in COLORS:
        if any(word in line for word in words):
            return color
    return (0.85, 0.88, 0.9)


def _running() -> tuple[int, str] | None:
    """(pid, display) of the running overlay, if any."""
    try:
        pid, _, display = PID_FILE.read_text().partition(" ")
        os.kill(int(pid), 0)
        return int(pid), display
    except (OSError, ValueError):
        return None


def overlay_is_up(display: str | None = None) -> bool:
    running = _running()
    return running is not None and (display is None or running[1] == display)


def ensure_overlay(display: str | None = None) -> bool:
    """Start the overlay on `display` (FTL's own Xephyr window, or the desktop when None)."""
    display = display or os.environ.get("DISPLAY", "")
    running = _running()
    if running is not None and running[1] == display:
        return True
    if running is not None:                 # on another display (FTL moved): restart it
        os.kill(running[0], signal.SIGTERM)
        time.sleep(0.5)
    OVERLAY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(OVERLAY_LOG, "ab") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--foreground"], cwd=PROJECT_DIR,
                         env=dict(os.environ, DISPLAY=display,
                                  FTL_DESKTOP_DISPLAY=os.environ.get("FTL_DESKTOP_DISPLAY") or os.environ.get("DISPLAY", "")),
                         stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(25):
        if overlay_is_up(display):
            return True
        time.sleep(0.2)
    return False


def ftl_window(pid: int) -> tuple[str, bool, tuple[int, int, int, int]] | None:
    """(window id, is it the active window, geometry x, y, w, h) of FTL."""
    found = subprocess.run(["xdotool", "search", "--pid", str(pid), "--name", "FTL"], capture_output=True, text=True)
    if not found.stdout.split():
        return None
    window = found.stdout.split()[-1]
    if game_display.current() == os.environ.get("DISPLAY"):
        active = window                     # FTL's own Xephyr window: always FTL's
    else:
        active = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True).stdout.strip()
    shape = subprocess.run(["xdotool", "getwindowgeometry", "--shell", window], capture_output=True, text=True)
    values = dict(line.split("=", 1) for line in shape.stdout.split() if "=" in line)
    try:
        geometry = (int(values["X"]), int(values["Y"]), int(values["WIDTH"]), int(values["HEIGHT"]))
    except (KeyError, ValueError):
        return None
    return window, active == window, geometry


def _click_through(xid: int):
    """Empty the window's input region (X Shape extension), so clicks reach FTL below it."""
    import ctypes
    import ctypes.util
    xlib = ctypes.CDLL(ctypes.util.find_library("X11"))
    xext = ctypes.CDLL(ctypes.util.find_library("Xext"))
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XFlush.argtypes = xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xext.XShapeCombineRectangles.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                             ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    display = xlib.XOpenDisplay(None)
    if display:
        xext.XShapeCombineRectangles(display, xid, 2, 0, 0, None, 0, 0, 0)   # ShapeInput, no rectangles, ShapeSet
        xlib.XFlush(display)
        xlib.XCloseDisplay(display)


class HotKeys:
    """F9-F11 grabbed on the root window, so they work while FTL has focus (FTL does not use them)."""

    KEYS = {"F9": "autoplay", "F10": "slower", "F11": "faster", "F12": "dashboard"}

    def __init__(self):
        import ctypes
        import ctypes.util
        from controller.x11_direct import _XEvent
        self.ctypes, self.event = ctypes, _XEvent()
        x = self.xlib = ctypes.CDLL(ctypes.util.find_library("X11"))
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int]
        x.XPending.argtypes = x.XFlush.argtypes = [ctypes.c_void_p]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        # A key another program already grabbed must not kill the overlay (X's default handler exits).
        self.on_error = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda *_: 0)
        x.XSetErrorHandler(self.on_error)
        self.display = x.XOpenDisplay(None)
        root = x.XDefaultRootWindow(self.display)
        self.actions = {}
        for name, action in self.KEYS.items():
            code = x.XKeysymToKeycode(self.display, x.XStringToKeysym(name.encode()))
            self.actions[code] = action
            for locks in (0, 2, 16, 18):           # also with Caps Lock and/or Num Lock on
                x.XGrabKey(self.display, code, locks, root, 0, 1, 1)
        x.XFlush(self.display)

    def pressed(self) -> list[str]:
        actions = []
        while self.xlib.XPending(self.display):
            self.xlib.XNextEvent(self.display, self.ctypes.byref(self.event))
            if self.event.input.type == 2:        # KeyPress
                action = self.actions.get(self.event.input.detail)
                if action:
                    actions.append(action)
        return actions


def _hex(color) -> str:
    return "#" + "".join(f"{int(c * 255):02x}" for c in color)


def run():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11, GLib, Gtk  # noqa: F401  (GdkX11 provides get_xid)

    import bot_settings
    from dashboard import GameLink, read_log

    link = GameLink()
    state = {"game": {}, "lines": [], "offset": None, "notice": None}
    keys = HotKeys()

    class Strip(Gtk.Window):
        """One click-through, never-focused panel (an override-redirect popup) holding a label."""

        def __init__(self):
            super().__init__(type=Gtk.WindowType.POPUP)
            self.set_accept_focus(False)
            visual = self.get_screen().get_rgba_visual()
            if visual is not None:
                self.set_visual(visual)
            self.label = Gtk.Label(xalign=0, yalign=0.5)
            self.label.set_margin_start(16)
            self.add(self.label)
            self.css = Gtk.CssProvider()
            self.get_style_context().add_provider(self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.alpha = None
            self.size = 14
            self.connect("realize", lambda _: _click_through(self.get_window().get_xid()))

        def place(self, x, y, w, h, alpha):
            if alpha != self.alpha:
                self.css.load_from_data(f"window {{ background-color: rgba(0,0,0,{alpha}); }}".encode())
                self.alpha = alpha
            self.set_size_request(w, h)
            self.resize(w, h)
            self.move(x, y)

    def span(content: str, color, size: int, bold=False) -> str:
        weight = ' weight="bold"' if bold else ""
        return (f'<span font_family="DejaVu Sans Mono" size="{size * 1024}" foreground="{_hex(color)}"{weight}>'
                f"{GLib.markup_escape_text(content)}</span>")

    def status_markup(size: int) -> str:
        """The top bar: the sector and the switches; the game itself shows the ship's state."""
        settings = state.get("settings", {})
        auto = settings.get("autoplay", True)
        speed = bot_settings.speed_multiplier(settings.get("speed", 0))
        game = state["game"]
        controls = []
        if game.get("connected") and game.get("sector"):
            controls.append((f"SECTOR {game['sector']}", (0.85, 0.88, 0.9), True))
        controls += [(f"[F9] AUTO-PLAY {'ON' if auto else 'OFF  (manual control)'}",
                     (0.25, 0.73, 0.31) if auto else (0.82, 0.6, 0.13), True),
                    (f"[F10 / F11] SPEED {speed:.1f}x", (0.74, 0.55, 1.0), True),
                    ("[F12] DASHBOARD", (0.35, 0.65, 1.0), True)]
        if not state["game"].get("connected"):
            controls.append(("bot not connected to FTL", (0.6, 0.6, 0.6), False))
        notice = state["notice"]
        if notice and time.monotonic() < notice[1]:
            controls.append((notice[0], (0.82, 0.6, 0.13), False))
        return "      ".join(span(text, color, size, bold=bold) for text, color, bold in controls)

    def handle_keys():
        try:
            for action in keys.pressed():
                settings = bot_settings.load()
                if action == "dashboard":
                    from dashboard import URL
                    desktop = os.environ.get("FTL_DESKTOP_DISPLAY") or os.environ.get("DISPLAY", "")
                    subprocess.Popen(["xdg-open", URL], env=dict(os.environ, DISPLAY=desktop),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                    state["notice"] = ("dashboard opened in your browser", time.monotonic() + 3)
                    continue
                if action == "autoplay":
                    settings = bot_settings.save({"autoplay": not settings["autoplay"]})
                    note = "auto-play on: the bot plays" if settings["autoplay"] else "manual control: the bot stops"
                else:
                    level = bot_settings.step_speed(settings["speed"], 1 if action == "faster" else -1)
                    settings = bot_settings.save({"speed": level})
                    problem = link.apply_speed(level)
                    note = problem or f"speed {bot_settings.speed_multiplier(level):.1f}x"
                state["settings"] = settings
                state["notice"] = (note, time.monotonic() + (8 if note.startswith("FTL was") else 3))
                if top.get_visible():
                    top.label.set_markup(status_markup(top.size))
        except Exception as error:
            print(f"[overlay] keys: {type(error).__name__}: {error}", flush=True)
        return True

    def log_markup(rows: int, size: int) -> str:
        return "\n".join(span(line[:200], line_color(line), size) for line in state["lines"][-rows:])

    top, bottom = Strip(), Strip()

    def tick():
        try:
            settings = bot_settings.load()
            state["settings"] = settings
            state["game"] = link.state()
            log = read_log(state["offset"])
            state["offset"] = log["offset"]
            state["lines"] = (state["lines"] + [l[9:] if l[:2].isdigit() else l for l in log["lines"]])[-50:]
            shown = False
            if settings["overlay"] and state["game"].get("connected") and link.client is not None:
                found = ftl_window(link.client.pid)
                if found and found[1]:
                    _, _, (wx, wy, ww, wh) = found
                    bar = (state["game"].get("bar") or [0, 0])[1]
                    if bar >= 36:      # letterboxed: use the black bars
                        rows = max(1, min(LOG_LINES, bar // 26))
                        top.place(wx, wy, ww, bar, 1.0)
                        bottom.place(wx, wy + wh - bar, ww, bar, 1.0)
                        top.size = max(12, min(20, int(bar * 0.33)))
                        top.label.set_markup(status_markup(top.size))
                        bottom.label.set_markup(log_markup(rows, max(10, min(15, int(bar / (rows * 1.7))))))
                    else:              # no bars: a translucent box in FTL's top-right corner
                        top.place(wx + ww - 900, wy + 8, 892, 34, 0.75)
                        bottom.place(wx + ww - 900, wy + 46, 892, 112, 0.75)
                        top.size = 13
                        top.label.set_markup(status_markup(top.size))
                        bottom.label.set_markup(log_markup(5, 13))
                    shown = True
            for strip in (top, bottom):
                strip.show_all() if shown else strip.hide()
        except Exception as error:   # keep the overlay alive through FTL restarts
            print(f"[overlay] {type(error).__name__}: {error}", flush=True)
        return True

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{os.getpid()} {os.environ.get('DISPLAY', '')}")
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, Gtk.main_quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, Gtk.main_quit)
    GLib.timeout_add(500, tick)
    GLib.timeout_add(50, handle_keys)
    tick()
    try:
        Gtk.main()
    finally:
        if PID_FILE.exists() and PID_FILE.read_text().split(" ")[0] == str(os.getpid()):
            PID_FILE.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--foreground", action="store_true", help="stay attached to this terminal")
    parser.add_argument("--stop", action="store_true", help="stop the overlay")
    args = parser.parse_args()
    if args.stop:
        try:
            os.kill(int(PID_FILE.read_text().split(" ")[0]), signal.SIGTERM)
            print("[overlay] Stopped.")
        except (OSError, ValueError):
            print("[overlay] Not running.")
    elif args.foreground:
        run()
    elif ensure_overlay():
        print("[overlay] Running in the background (shows while FTL is the active window).")
    else:
        print(f"[overlay] Did not start; see {OVERLAY_LOG}")
        sys.exit(1)


if __name__ == "__main__":
    main()
