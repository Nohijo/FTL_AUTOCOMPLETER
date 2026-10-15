"""Send keyboard and mouse input to the FTL window (X11, via xdotool).

Coordinates passed to this module are FTL game coordinates (the 1280x720
space the game logic uses). They are converted to window pixels with the
scaling FTL itself uses, so fullscreen, stretched and letterboxed modes all
work.

While FTL is the active window, input goes through XTEST (xdotool). The bot
never activates FTL itself: before every event it checks that FTL still has
focus. If it lost focus, the sequence stops with FocusLost. In auto-play
(direct_mode) every event is sent straight to the FTL window with XSendEvent
instead (controller/x11_direct.py), which never moves your pointer or types into
the window you are using, and works while FTL is behind other windows.
"""

import os
import subprocess
import time
from pathlib import Path

import game_display

from controller.x11_direct import DirectInput
from controller.xtest_input import XTestInput
from observer.memory.reader import GameSnapshot, ScreenMapping

_XDOTOOL_COMMANDS = {"mousemove", "click", "mousedown", "mouseup", "key", "keydown", "keyup", "sleep"}

# Defaults from Settings::ResetHotkeys in FTL.amd64 1.6.12 (extracted with gdb).
DEFAULT_HOTKEYS = {
    "crew1": 282, "crew2": 283, "crew3": 284, "crew4": 285,
    "crew5": 286, "crew6": 287, "crew7": 288, "crew8": 289,
    "crew_all": 113, "lockdown": 112, "pause": 32, "jump": 106,
    "ship_info": 117, "ship_inv": 105, "options": 111,
    "open": 122, "close": 120, "activate_cloak": 99, "send_tele": 116,
    "ret_tele": 114, "start_hacking": 110, "activate_battery": 98,
    "drone1": 53, "drone2": 54, "drone3": 55,
    "weapon1": 49, "weapon2": 50, "weapon3": 51, "weapon4": 52,
    "autofire": 118, "force_autofire": 306,
    "shields": 97, "engines": 115, "oxygen": 102, "medbay": 100,
    "teleporter": 103, "cloaking": 104, "mind": 107, "hacking": 108,
    "artillery": 121, "weapons": 119, "drones": 101,
}

# SDL 1.2 key codes (as stored in settings.ini) -> X keysym names for xdotool.
_SPECIAL_KEYS = {
    8: "BackSpace", 9: "Tab", 13: "Return", 27: "Escape", 32: "space",
    45: "minus", 61: "equal", 91: "bracketleft", 93: "bracketright",
    304: "Shift_L", 305: "Control_R", 306: "Control_L", 307: "Alt_R", 308: "Alt_L",
}


def keysym_for(code: int) -> str | None:
    if code in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[code]
    if 282 <= code <= 293:
        return f"F{code - 281}"
    if 48 <= code <= 57 or 97 <= code <= 122:
        return chr(code)
    return None


def load_hotkeys(settings_path: Path) -> dict[str, int]:
    hotkeys = dict(DEFAULT_HOTKEYS)
    if settings_path.exists():
        for line in settings_path.read_text(errors="replace").splitlines():
            name, sep, value = line.partition("=")
            if sep and name in hotkeys and value.strip().lstrip("-").isdigit():
                hotkeys[name] = int(value)
    return hotkeys


class FocusLost(Exception):
    """FTL stopped being the focused window; the current input sequence is abandoned."""


class GameInput:
    def __init__(self, pid: int, settings_path: Path, dry_run: bool = True, focus_probe=None):
        self.pid = pid
        self.dry_run = dry_run
        self.focus_probe = focus_probe     # callable -> bool: is FTL focused right now?
        self.last_escape = 0.0             # when the bot last pressed Escape (monotonic time)
        self.hotkeys = load_hotkeys(settings_path)
        # FTL in its own Xephyr window (game_display.py): every xdotool call targets that
        # display, where FTL always has focus and nothing else lives.
        self.display = game_display.current()
        self.nested = self.display is not None
        self.env = dict(os.environ, DISPLAY=self.display) if self.nested else None
        self.window = None if dry_run else self._find_window()
        self.log: list[str] = []
        self.direct_mode = False           # auto-play: every event goes straight to FTL's window
        self.direct: DirectInput | None = None
        # In FTL's own display, XTEST through one open connection: no xdotool process per event.
        self.xtest: XTestInput | None = None
        if self.nested and not dry_run:
            try:
                self.xtest = XTestInput(self.display)
            except (OSError, RuntimeError):
                self.xtest = None

    def _find_window(self, wait: float = 30.0) -> str:
        """FTL's window; a freshly launched FTL takes a few seconds to open it."""
        deadline = time.monotonic() + wait
        while True:
            result = subprocess.run(["xdotool", "search", "--pid", str(self.pid), "--name", "FTL"],
                                    capture_output=True, text=True, env=self.env)
            windows = result.stdout.split()
            if windows:
                break
            if time.monotonic() > deadline:
                raise RuntimeError(f"No FTL window found for PID {self.pid}")
            time.sleep(0.5)
        if self.nested:
            # No window manager in there: give FTL the keyboard focus explicitly.
            subprocess.run(["xdotool", "windowfocus", windows[-1]], env=self.env, check=False,
                           capture_output=True)
        return windows[-1]

    def ftl_is_active(self) -> bool:
        """Ask the X server which window is active and whether it belongs to FTL.
        FTL's own focus flag (CApp.focus) can stay False after a keyboard grab such as a
        screenshot, so it is not used for this. Also refreshes the window id.
        In its own Xephyr window FTL is the only window and always has focus."""
        if self.nested:
            if self.window is None and not self.dry_run:
                self.window = self._find_window()
            return True
        result = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True)
        window = result.stdout.strip()
        if not window:
            return False
        owner = subprocess.run(["xdotool", "getwindowpid", window], capture_output=True, text=True)
        if owner.stdout.strip() != str(self.pid):
            return False
        self.window = window
        return True

    def _xdotool(self, *args: str):
        self.log.append("xdotool " + " ".join(args))
        if self.dry_run:
            return
        if self.direct_mode:
            # Auto-play: straight into FTL's window, whatever window is active, without
            # touching the real pointer or keyboard focus.
            self._send_direct(args)
            return
        if self.xtest is not None:
            if self.window is None:
                self.window = self._find_window()
            self._replay(args, self.xtest)
            return
        # XTEST goes to the active window, so it is checked before every single event: if the
        # player switched to another window mid-sequence, stop instead of typing into it.
        if self.ftl_is_active():
            args = tuple(self.window if a == "<ftl>" else a for a in args)
            subprocess.run(["xdotool", *args], check=False, env=self.env)
        else:
            raise FocusLost()

    def _send_direct(self, args: tuple[str, ...]):
        """Replay an xdotool command line with XSendEvent into the FTL window only."""
        if self.direct is None:
            self.direct = DirectInput(self.display)
        self._replay(args, self.direct)

    def _replay(self, args: tuple[str, ...], backend):
        """Run an xdotool command line through an input backend (move / button / key)."""
        window = int(self.window)
        i = 0
        while i < len(args):
            command, i = args[i], i + 1
            operands = []
            while i < len(args) and args[i] not in _XDOTOOL_COMMANDS:
                if args[i] == "--window":
                    i += 1                  # its value is the FTL window anyway
                elif not args[i].startswith("--"):
                    operands.append(args[i])
                i += 1
            if command == "mousemove":
                backend.move(window, int(operands[0]), int(operands[1]))
            elif command == "click":
                backend.button(window, int(operands[0]), True)
                time.sleep(self.PRESS_SECONDS)
                backend.button(window, int(operands[0]), False)
            elif command in ("mousedown", "mouseup"):
                backend.button(window, int(operands[0]), command == "mousedown")
            elif command in ("keydown", "keyup"):
                for keysym in operands:
                    backend.key(window, keysym, command == "keydown")
            elif command == "sleep":
                time.sleep(float(operands[0]))
            elif command == "key":
                for combo in operands:          # e.g. "f" or "shift+f"
                    *modifiers, keysym = combo.split("+")
                    held = [{"shift": "Shift_L", "ctrl": "Control_L", "alt": "Alt_L"}.get(m.lower(), m)
                            for m in modifiers]
                    for key in held:
                        backend.key(window, key, True)
                    backend.key(window, keysym, True)
                    time.sleep(self.PRESS_SECONDS)     # like xdotool: FTL can miss a press and release in one frame
                    backend.key(window, keysym, False)
                    for key in reversed(held):
                        backend.key(window, key, False)

    def background_keys_work(self) -> bool:
        """Keys sent straight to FTL's window only arrive when FTL runs without an input-method
        server (ftl-host starts it with XMODIFIERS=@im=none); an FTL started before that, or
        from the desktop, gets them filtered by IBus while it is not focused."""
        try:
            environ = Path(f"/proc/{self.pid}/environ").read_bytes().split(b"\0")
        except OSError:
            return False
        modifiers = next((v for v in environ if v.startswith(b"XMODIFIERS=")), b"XMODIFIERS=")
        return modifiers in (b"XMODIFIERS=", b"XMODIFIERS=@im=none")

    def ensure_focus(self, snapshot: GameSnapshot):
        """Never take focus: input only goes to FTL while it is the active window
        (or, in background mode, straight to its window)."""
        if not self.dry_run and not self.direct_mode and not self.ftl_is_active():
            raise FocusLost()

    # --- primitives ----------------------------------------------------------

    def press_key(self, keysym: str, snapshot: GameSnapshot):
        """Press a raw key (e.g. "1" to pick the first event choice with dialog_keys=1)."""
        self.ensure_focus(snapshot)
        self._xdotool("key", keysym)

    def press(self, action: str, snapshot: GameSnapshot, shift: bool = False) -> bool:
        keysym = keysym_for(self.hotkeys.get(action, 0))
        if keysym is None:
            self.log.append(f"# no key bound for {action}")
            return False
        self.ensure_focus(snapshot)
        if shift:
            # FTL reads Shift as held while it handles the key, so keep it down across frames.
            self._xdotool("keydown", "Shift_L", "sleep", "0.05", "key", keysym, "sleep", "0.05", "keyup", "Shift_L")
        else:
            self._xdotool("key", keysym)
        return True

    # FTL picks up pointer motion once per frame. A click sent right after a move is
    # handled at the old position (seen in calibration: orders landed in the last
    # hovered room), so wait for the move to complete and give the game a few frames.
    SETTLE_SECONDS = 0.045
    PRESS_SECONDS = 0.015              # between a key / button going down and up again

    def _move(self, wx: int, wy: int):
        """Put the pointer on window pixel (wx, wy). --sync waits until the pointer has moved,
        and when it is already there xdotool waits ~15 s for a move that never comes (seen
        as the bot hovering a store or upgrade button "forever"). So step one pixel aside
        first, in the same xdotool call so the two moves stay in order."""
        aside = wx - 1 if wx > 0 else wx + 1
        self._xdotool("mousemove", "--window", "<ftl>", str(aside), str(wy),
                      "mousemove", "--sync", "--window", "<ftl>", str(wx), str(wy))

    def hover(self, game_x: float, game_y: float, mapping: ScreenMapping):
        self._move(*mapping.game_to_window(game_x, game_y))
        if not self.dry_run:
            time.sleep(self.SETTLE_SECONDS)

    def click(self, game_x: float, game_y: float, mapping: ScreenMapping, button: int = 1):
        self._move(*mapping.game_to_window(game_x, game_y))
        if not self.dry_run:
            time.sleep(self.SETTLE_SECONDS)
        self._xdotool("click", str(button))
        if not self.dry_run:
            time.sleep(self.SETTLE_SECONDS / 2)

    # --- game actions --------------------------------------------------------

    def target_enemy_room(self, weapon_index: int, room, snapshot: GameSnapshot) -> bool:
        """Arm weapon N (0-based) and click the centre of an enemy room."""
        if snapshot.enemy_origin is None or weapon_index > 3:
            return False
        if not self.press(f"weapon{weapon_index + 1}", snapshot):
            return False
        ox, oy = snapshot.enemy_origin
        self.slow_click(ox + room.x + room.w / 2, oy + room.y + room.h / 2, snapshot.mapping)
        return True

    def slow_click(self, game_x: float, game_y: float, mapping: ScreenMapping):
        """Deliberate left click: nudge onto the spot so the game sees fresh motion there,
        then press and release on separate frames."""
        self.hover(game_x + 3, game_y + 3, mapping)
        self.hover(game_x, game_y, mapping)
        if not self.dry_run:
            time.sleep(0.05)
        self._xdotool("mousedown", "1")
        if not self.dry_run:
            time.sleep(0.04)
        self._xdotool("mouseup", "1")
        if not self.dry_run:
            time.sleep(self.SETTLE_SECONDS)

    def cancel_aim(self):
        """Right-click cancels a weapon that is still selected for aiming."""
        self._xdotool("click", "3")

    def select_crew(self, crew, snapshot: GameSnapshot) -> bool:
        """Select exactly this crew member with their crew panel hotkey (F1-F8 by default)."""
        number = snapshot.crew_hotkeys.get(crew.address)
        if number is None or not 1 <= number <= 8:
            self.log.append(f"# {crew.name} has no crew hotkey")
            return False
        return self.press(f"crew{number}", snapshot)

    def order_selected_crew_to_room(self, room, snapshot: GameSnapshot):
        """Right-click a room of the player ship: selected crew walk there."""
        ox, oy = snapshot.player_origin
        self.click(ox + room.x + room.w / 2, oy + room.y + room.h / 2, snapshot.mapping, button=3)

    def drain_log(self) -> list[str]:
        lines, self.log = self.log, []
        return lines
