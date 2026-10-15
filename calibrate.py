"""Check that the bot's mouse and keyboard input lands where it should in FTL.

    ftl-calibrate

Needs a run in progress (FTL is started through ftl-host if it is not
running yet). Switch to the FTL window
when asked. The test only does harmless things:
  1. hovers the mouse over each crew member and over the centre of each
     room, then compares the position FTL recorded with the intended one
  2. presses the pause key twice and checks that the pause state flipped
     and came back
"""

import struct
import subprocess
import sys
import time

from config import FTL_SAVE_DIR
from controller.game_input import GameInput
from ftl_host import SOCKET_PATH, ensure_host
from observer.memory import layout as L
from observer.memory.process import AttachError, HostClient
from observer.memory.reader import read_snapshot


def game_mouse(proc) -> tuple[int, int]:
    return struct.unpack("<ii", proc.read(L.MOUSE_CONTROL + L.MOUSE_POSITION, 8))


def main():
    try:
        ensure_host()
        proc = HostClient(SOCKET_PATH)
    except AttachError as error:
        print(f"[calibrate] {error}")
        sys.exit(1)

    snapshot = read_snapshot(proc)
    if snapshot.screen != "in_run" or snapshot.player is None:
        print(f"[calibrate] Start or continue a run first (screen: {snapshot.screen}).")
        sys.exit(1)

    print("[calibrate] Switch to the FTL window now (waiting up to 60 s)...")
    deadline = time.monotonic() + 60
    probe = GameInput(proc.pid, FTL_SAVE_DIR / "settings.ini", dry_run=True)
    while not probe.ftl_is_active():
        if time.monotonic() > deadline:
            print("[calibrate] FTL never got focus; giving up.")
            sys.exit(1)
        time.sleep(0.2)
    print("[calibrate] FTL focused. Hands off the mouse for a few seconds...")
    time.sleep(1.5)

    game = GameInput(proc.pid, FTL_SAVE_DIR / "settings.ini", dry_run=False)
    snapshot = read_snapshot(proc)
    mapping, (ox, oy) = snapshot.mapping, snapshot.player_origin
    print(f"[calibrate] mapping {mapping}, player ship origin {(ox, oy)}")

    points = [(f"crew {c.name}", ox + c.x, oy + c.y) for c in snapshot.player.crew]
    points += [(f"room {r.room_id}", ox + r.x + r.w / 2, oy + r.y + r.h / 2) for r in snapshot.player.rooms]

    worst = 0
    for label, gx, gy in points:
        wx, wy = mapping.game_to_window(gx, gy)
        for attempt in range(3):  # a hand on the mouse can move the pointer mid-test
            subprocess.run(["xdotool", "mousemove", "--window", game.window, str(wx), str(wy)], check=False, env=game.env)
            time.sleep(0.15)
            seen = game_mouse(proc)
            error = max(abs(seen[0] - round(gx)), abs(seen[1] - round(gy)))
            if error <= 1:
                break
        worst = max(worst, error)
        print(f"  {label:<14} wanted ({round(gx)},{round(gy)})  FTL saw {seen}  off by {error}px")
    print(f"[calibrate] mouse: worst error {worst}px -> {'OK' if worst <= 1 else 'MISMATCH'}")

    before = read_snapshot(proc).paused
    game.press("pause", read_snapshot(proc))
    time.sleep(0.3)
    middle = read_snapshot(proc).paused
    game.press("pause", read_snapshot(proc))
    time.sleep(0.3)
    after = read_snapshot(proc).paused
    keys_ok = middle != before and after == before
    print(f"[calibrate] keys: paused {before} -> {middle} -> {after} -> {'OK' if keys_ok else 'MISMATCH'}")

    check_crew_orders(proc, game)


def check_crew_orders(proc, game: GameInput):
    """Send one crew member to a neighbouring room and back, checking each step in memory."""
    snapshot = read_snapshot(proc)
    crew_list = [c for c in snapshot.player.crew
                 if c.ship_id == snapshot.player.ship_id and not c.dead and c.address in snapshot.crew_hotkeys]
    if not crew_list:
        print("[calibrate] crew orders: no selectable crew found -> SKIPPED")
        return
    crew = min(crew_list, key=lambda c: (c.manning >= 0, c.manning))  # prefer someone idle
    home = next(r for r in snapshot.player.rooms if r.room_id == crew.room_id)
    neighbour = min((r for r in snapshot.player.rooms if r.room_id != home.room_id),
                    key=lambda r: abs(r.x - home.x) + abs(r.y - home.y))
    print(f"[calibrate] crew orders: {crew.name} (hotkey crew{snapshot.crew_hotkeys[crew.address]}) "
          f"room {home.room_id} -> room {neighbour.room_id} -> back")

    def find(snap):
        return next(c for c in snap.player.crew if c.address == crew.address)

    def set_paused(want: bool):
        if read_snapshot(proc).paused != want:
            game.press("pause", read_snapshot(proc))
            time.sleep(0.25)

    ok = True
    set_paused(True)
    for target in (neighbour, home):
        snapshot = read_snapshot(proc)
        game.select_crew(crew, snapshot)
        time.sleep(0.15)
        selected = read_snapshot(proc).selected_crew
        game.order_selected_crew_to_room(target, snapshot)
        time.sleep(0.2)
        queued = find(read_snapshot(proc))
        # Orders given while paused are only queued; let time run briefly so the walk starts.
        set_paused(False)
        time.sleep(1.2)
        set_paused(True)
        moved = find(read_snapshot(proc))
        in_target = lambda c: target.x <= c.goal_x < target.x + target.w and target.y <= c.goal_y < target.y + target.h
        step_ok = crew.address in selected and (moved.room_id == target.room_id or in_target(moved))
        print(f"  to room {target.room_id}: selected={crew.address in selected} ({len(selected)} total) | "
              f"right after order: slot_room={queued.slot_room} new_path={queued.new_path} | "
              f"after 1.2 s: room {moved.room_id}, walking to ({moved.goal_x:.0f},{moved.goal_y:.0f}), "
              f"slot_room={moved.slot_room} -> {'OK' if step_ok else 'FAILED'}")
        ok = ok and step_ok
        if not ok:
            break  # the return trip proves nothing if the first move failed
    print(f"[calibrate] crew orders -> {'OK' if ok else 'MISMATCH'}")


if __name__ == "__main__":
    main()
