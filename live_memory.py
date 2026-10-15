"""Show FTL's live state, read straight from process memory.

    ftl-live                 live dashboard through ftl-host (started if needed)
    ftl-live --json          one JSON snapshot per change instead
    ftl-live --launch        launch FTL directly, without ftl-host
    ftl-live --attach        attach to an FTL this tool did not start
                             (fails under ptrace_scope=1, see process.py)
"""

import argparse
import json
import sys
import time

from config import FTL_DATA_DIR, LOG_DIR
from ftl_host import SOCKET_PATH, ensure_host
from observer.memory.process import AttachError, FTLProcess, HostClient, MemoryReadError
from observer.memory.reader import GameSnapshot, ShipInfo, read_snapshot

CLEAR = "\x1b[H\x1b[2J"


def bar(current: float, maximum: float, width: int = 20) -> str:
    if maximum <= 0:
        return "-" * width
    filled = max(0, min(width, round(width * current / maximum)))
    return "#" * filled + "." * (width - filled)


def format_ship(ship: ShipInfo, title: str) -> list[str]:
    lines = [
        f"== {title}: {ship.name or ship.blueprint} ({ship.blueprint})"
        + ("  [DESTROYED]" if ship.destroyed else "")
        + ("  [SURRENDERED]" if ship.surrendered else "")
        + ("  [ESCAPING]" if ship.escaping else ""),
        f"   Hull    {ship.hull:>3}/{ship.max_hull:<3} [{bar(ship.hull, ship.max_hull)}]",
        f"   Shields {ship.shield_layers}/{ship.max_shield_layers}  charge {ship.shield_charge:4.2f}"
        + (f"  super {ship.super_shield}" if ship.super_shield else ""),
    ]
    if ship.is_player:
        lines.append(
            f"   Scrap {ship.scrap}  Fuel {ship.fuel}  Missiles {ship.missiles}  Drone parts {ship.drone_parts}"
        )
    reactor = (f"{ship.reactor_used}/{ship.reactor_total}"
               if ship.reactor_total is not None else "?")
    jump = "READY" if ship.jump_ready else f"{ship.jump_charge:5.1f}/{ship.jump_goal:.1f}"
    oxygen = f"{ship.oxygen_total:5.1f}" if ship.oxygen_total is not None else "?"
    lines.append(f"   Reactor {reactor}  FTL {jump}  O2 {oxygen}")

    lines.append("   Systems:")
    for system in sorted(ship.systems.values(), key=lambda s: s.type_id):
        flags = []
        if system.on_fire:
            flags.append("FIRE")
        if system.breached:
            flags.append("BREACH")
        if system.ion_lock:
            flags.append(f"ION x{system.ion_lock}")
        if system.manned:
            flags.append("manned")
        if system.hacked:
            flags.append("HACKED")
        lines.append(
            f"     {system.type_id:>2} {system.name:<10} power {system.power}/{system.level}"
            f"  health {system.health}/{system.level}  cap {system.max_level}"
            f"  room {system.room_id:<2} {' '.join(flags)}"
        )

    if ship.weapons:
        lines.append("   Weapons:")
        for index, weapon in enumerate(ship.weapons, start=1):
            state = "READY" if weapon.ready else f"{weapon.charge:4.1f}/{weapon.charge_goal:.1f}s"
            lines.append(
                f"     {index}. {weapon.blueprint:<22} {weapon.weapon_type:<9}"
                f" {'ON ' if weapon.powered else 'off'} {state:<11}"
                f" dmg {weapon.damage} x{weapon.shots}"
                + (" auto" if weapon.auto_firing else "")
                + (" targeted" if weapon.targeted else "")
            )

    if ship.crew:
        lines.append("   Crew:")
        for crew in ship.crew:
            flags = []
            if crew.intruder:
                flags.append("INTRUDER")
            if crew.fighting:
                flags.append("fighting")
            if crew.repairing:
                flags.append("repairing")
            if crew.extinguishing:
                flags.append("extinguishing")
            if crew.manning >= 0:
                flags.append(f"manning sys {crew.manning}")
            if crew.dead:
                flags.append("DEAD")
            lines.append(
                f"     {crew.name or '?':<14} {crew.species:<9} hp {crew.health:5.1f}/{crew.max_health:<5.1f}"
                f" room {crew.room_id:<2} ship {crew.ship_id} {' '.join(flags)}"
            )

    burning = [f"{room.room_id}x{room.fires}" for room in ship.rooms if room.fires > 0]
    breached = [f"{room.room_id}x{room.breaches}" for room in ship.rooms if room.breaches > 0]
    low_oxygen = [f"{room.room_id}:{room.oxygen:.0f}%" for room in ship.rooms
                  if room.oxygen is not None and room.oxygen < 50]
    if burning or breached or low_oxygen:
        lines.append(f"   Fires (room x tiles) {', '.join(burning) or '-'}   "
                     f"Breaches {', '.join(breached) or '-'}   Low O2 {', '.join(low_oxygen) or '-'}")
    return lines


def format_snapshot(snapshot: GameSnapshot) -> str:
    if snapshot.screen != "in_run":
        return f"FTL screen: {snapshot.screen}\n"

    flags = [
        name for name, on in (
            ("PAUSED", snapshot.paused), ("menu", snapshot.menu_open), ("EVENT", snapshot.event_open),
            ("STORE", snapshot.store_open), ("ship screen", snapshot.ship_screen_open),
            ("map", snapshot.map_open), ("GAME OVER", snapshot.game_over),
            ("nebula", snapshot.nebula), ("storm", snapshot.storm),
        ) if on
    ]
    lines = [
        f"Sector {snapshot.sector}  beacon event: {snapshot.location_event or '-'}"
        + ("  (store here)" if snapshot.location_has_store else ""),
        f"State: {' | '.join(flags) or 'running'}",
        "",
    ]
    if snapshot.event_open:
        lines.append("Event: " + snapshot.event_text.replace("\n", " ")[:300])
        for index, choice in enumerate(snapshot.event_choices, start=1):
            lines.append(f"  {index}. {choice.text.replace(chr(10), ' ')[:120]}")
        lines.append("")
    if snapshot.player:
        lines += format_ship(snapshot.player, "PLAYER")
        lines.append("")
    if snapshot.enemy:
        lines += format_ship(snapshot.enemy, "ENEMY")
        lines.append("")
    if snapshot.errors:
        lines.append(f"Read errors ({len(snapshot.errors)}): " + "; ".join(snapshot.errors[:3]))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--launch", action="store_true", help="launch FTL directly instead of using ftl_host")
    parser.add_argument("--attach", action="store_true", help="attach to a running FTL directly")
    parser.add_argument("--pid", type=int, help="PID to attach to (implies --attach)")
    parser.add_argument("--json", action="store_true", help="print JSON snapshots instead of a dashboard")
    parser.add_argument("--hz", type=float, default=4.0, help="reads per second (default 4)")
    args = parser.parse_args()

    try:
        if args.attach or args.pid:
            proc = FTLProcess.attach(args.pid)
        elif args.launch:
            proc = FTLProcess.launch(FTL_DATA_DIR, LOG_DIR / "ftl_stdout.log")
            print(f"[live_memory] Launched FTL (PID {proc.pid}). Waiting for it to start...")
            time.sleep(2)
        else:
            ensure_host()
            proc = HostClient(SOCKET_PATH)
    except AttachError as error:
        print(f"[live_memory] {error}")
        sys.exit(1)

    if not proc.binary_matches():
        print("[live_memory] WARNING: this FTL.amd64 is not the 1.6.12 build the offsets were taken from.")

    last_output = None
    try:
        while proc.alive():
            try:
                snapshot = read_snapshot(proc)
            except MemoryReadError as error:
                snapshot = GameSnapshot(screen="no_game", errors=[str(error)])

            if args.json:
                output = json.dumps(snapshot.to_dict(), default=str)
                if output != last_output:
                    print(output, flush=True)
            else:
                output = format_snapshot(snapshot)
                if output != last_output:
                    sys.stdout.write(CLEAR + output)
                    sys.stdout.flush()
            last_output = output
            time.sleep(1.0 / args.hz)
        print("[live_memory] FTL exited.")
    except KeyboardInterrupt:
        print("\n[live_memory] Stopped.")


if __name__ == "__main__":
    main()
