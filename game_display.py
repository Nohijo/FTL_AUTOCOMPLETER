"""FTL's own X display: a Xephyr window on your desktop with FTL running inside.

Inside that nested display FTL always has focus, so it never pauses itself and the
bot can drive it with ordinary XTEST input (xdotool) aimed at that display only:
your real mouse and keyboard, and every other window, are never touched. Switch to
other windows freely; the game keeps running in its window. To play yourself, turn
auto-play off (F9) and click into the window (Ctrl+Shift releases a grabbed mouse,
as in any Xephyr window).

ftl_host.py starts the display before launching FTL and stops it when FTL exits;
everything else finds it through data/game_display.json.
"""

import json
import os
import subprocess
import time
from pathlib import Path

from config import LOG_DIR, PROJECT_DIR

DISPLAY_FILE = PROJECT_DIR / "data" / "game_display.json"
# FTL's native 1280x720 plus a 60 px black bar above and below for the overlay's switches
# and log (FTL letterboxes itself in fullscreen).
SIZE = (1280, 840)
TITLE = "FTL - bot window (auto-play: F9, speed: F10/F11)"
FIRST_DISPLAY = 7


def _socket(number: int) -> Path:
    return Path(f"/tmp/.X11-unix/X{number}")


def start() -> tuple[str, subprocess.Popen]:
    """Start a Xephyr display for FTL and record it. Returns (":N", process)."""
    number = next(n for n in range(FIRST_DISPLAY, FIRST_DISPLAY + 40)
                  if not _socket(n).exists() and not Path(f"/tmp/.X{n}-lock").exists())
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / "game_display.log", "ab")
    process = subprocess.Popen(
        ["Xephyr", f":{number}", "-screen", f"{SIZE[0]}x{SIZE[1]}", "-title", TITLE,
         "-ac", "-br", "-noreset", "-nolisten", "tcp", "+extension", "GLX"],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 10
    while not _socket(number).exists():
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError(f"Xephyr did not start (see {LOG_DIR / 'game_display.log'})")
        time.sleep(0.1)
    display = f":{number}"
    DISPLAY_FILE.write_text(json.dumps({"display": display, "pid": process.pid}))
    return display, process


def stop(process: subprocess.Popen | None):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    DISPLAY_FILE.unlink(missing_ok=True)


def current() -> str | None:
    """The display FTL runs on, if it runs in its own Xephyr window; else None."""
    try:
        data = json.loads(DISPLAY_FILE.read_text())
        number = int(data["display"].lstrip(":"))
        os.kill(int(data["pid"]), 0)
    except (OSError, ValueError, KeyError):
        return None
    return data["display"] if _socket(number).exists() else None
