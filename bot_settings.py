"""Switches the dashboard flips and the bot follows, kept in data/bot_settings.json.

    autoplay     the bot plays (also while FTL is behind other windows; its input
                 goes straight to the FTL window, never to the window you are
                 using). Off = manual control: the bot only watches and records.
    speed        FTL's game-speed level, 0 = normal (see layout.CFPS_SPEED_LEVEL)
    overlay      show the bot's status over the game (overlay.py)
"""

import json
import os

from config import PROJECT_DIR
from observer.memory import layout as L

SETTINGS_FILE = PROJECT_DIR / "data" / "bot_settings.json"
DEFAULTS = {"autoplay": True, "speed": 0, "overlay": True}
SPEED_STEPS = [0, 1, 2, 3, 5, 7, 10, 14, 18, 21, 30, 42, 58, 78, 112]  # nominal 1x .. 50x; the real
# speed is lower once FTL's per-frame cap is reached (about level 10 at ~140 fps: ~4.4x)


def step_speed(level: int, direction: int) -> int:
    """The next offered speed level up (direction 1) or down (-1) from level."""
    if direction > 0:
        return next((step for step in SPEED_STEPS if step > level), SPEED_STEPS[-1])
    return next((step for step in reversed(SPEED_STEPS) if step < level), SPEED_STEPS[0])


def speed_multiplier(level: int) -> float:
    """The nominal speed of a level, before FTL's per-frame cap (the overlay and dashboard
    show the real speed, measured from FTL's game clock)."""
    return (16 + 7 * level) / 16


def load() -> dict:
    settings = dict(DEFAULTS)
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
        if "autoplay" not in stored and isinstance(stored.get("background"), bool):
            stored["autoplay"] = True        # older files: the bot always played then
        settings.update({k: v for k, v in stored.items() if k in DEFAULTS and type(v) is type(DEFAULTS[k])})
    except (OSError, ValueError):
        pass
    low, high = L.SPEED_LEVEL_RANGE
    settings["speed"] = min(max(settings["speed"], low), high)
    return settings


def save(settings: dict) -> dict:
    merged = load()
    merged.update({k: v for k, v in settings.items() if k in DEFAULTS and type(v) is type(DEFAULTS[k])})
    low, high = L.SPEED_LEVEL_RANGE
    merged["speed"] = min(max(merged["speed"], low), high)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(merged, indent=2))
    os.replace(temporary, SETTINGS_FILE)    # readers never see a half-written file
    return merged


# FTL caps the game time of one frame, so above ~level 20 the speed only grows with the frame
# rate, and in its software-rendered window drawing limits that. The top steps add turbo:
# draw one frame in N, so N times more game frames run per second (layout.RENDER_*).
TURBO: tuple = ()   # off: FTL advances some animations (the jump sequence) only on drawn frames


def turbo_for(level: int) -> int:
    return next((every for threshold, every in TURBO if level >= threshold), 1)


def apply_speed(proc, level: int) -> str | None:
    """Set FTL's speed level (and turbo) and read them back. Returns a problem, or None."""
    try:
        if proc.i32(L.CFPS_SPEED_LEVEL) != level:
            proc.set_speed_level(level)
            if proc.i32(L.CFPS_SPEED_LEVEL) != level:
                return "this ftl-host is too old to change the speed; Save and Quit FTL and start the bot again"
    except Exception:   # an ftl-host from before the speed control answers with a failed read
        return "FTL was started before the speed control existed: Save and Quit FTL, then start the bot again"
    every = turbo_for(level)
    try:
        if proc.render_skip() != every:
            proc.set_render_skip(every)
            if proc.render_skip() != every:
                return "turbo did not take"
    except Exception as error:
        if every == 1:
            return None
        return f"turbo needs a newer ftl-host (FTL restarts with it): {error}"
    return None
