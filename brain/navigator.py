"""Decide when to jump, which route to fly through the sector, and which sector to enter next.

The route is chosen for value, not speed: every simple path from the current
beacon to the exit is considered, as long as each beacon is reached before the
rebel fleet takes it, the exit is still free on arrival and the fuel lasts.
A path scores the events it collects (scrap, fuel, missiles, weapons, crew,
repairs - see brain/event_chooser.py), stores when they are needed, and
penalties for environmental hazards. The bot takes the first jump of the best
path and plans again after every jump.

The fleet's per-jump advance comes from StarMap::GetNextDangerMove
(StarMapState.fleet_move). It moves when we jump, not over time, so waiting at
a safe beacon to heal and repair is free.
"""

import math
from dataclasses import dataclass

from brain.live_combat import HEALED
from observer.memory.adapter import in_combat
from observer.memory.reader import GameSnapshot
from observer.memory.starmap import Beacon, StarMapState

MAX_ROUTE_JUMPS = 9          # how far ahead routes are searched
MAX_EXPANSIONS = 300_000     # hard cap on search work per decision
FLEET_SAFETY = 25.0          # map pixels of margin to keep from the fleet's edge
DISCOUNT = 0.97              # later rewards count slightly less (more can go wrong before then)
HAZARD_PENALTY = 6.0         # beacons with a sun, asteroids, ion storm, pulsar...
LEAVE_SECTOR_BONUS = 3.0     # small preference for moving on when nothing is worth the detour

# Rough preference for the next sector by type: homeworld sectors tend to have
# good events, nebulas switch off sensors.
SECTOR_PREFERENCE = {
    "CIVILIAN": 3, "ENGI": 3, "ZOLTAN": 3, "MANTIS": 1, "ROCK": 1,
    "PIRATE": 1, "HOSTILE": 1, "REBEL": 0, "SLUG": -1, "NEBULA": -1,
}


@dataclass
class Plan:
    beacon: Beacon | None            # None with leave_sector=True: take the exit
    score: float
    notes: list[str]
    leave_sector: bool = False


def jump_blockers(snapshot: GameSnapshot) -> list[str]:
    """Reasons not to jump yet; empty when it is time to go."""
    if snapshot.screen != "in_run" or snapshot.player is None:
        return ["not in a run"]
    player = snapshot.player
    for flag, reason in ((snapshot.event_open, "event open"), (snapshot.store_open, "store open"),
                         (snapshot.menu_open, "menu open"), (snapshot.ship_screen_open, "ship screen open"),
                         (snapshot.game_over, "game over")):
        if flag:
            return [reason]
    if in_combat(snapshot):
        return ["in combat"]
    if not player.jump_ready:
        return ["FTL drive charging"]
    helm = player.systems.get("pilot")
    if helm is not None and helm.level < 2 and not helm.manned:
        # FTL will not jump with an empty helm (the map does not even open); the crew
        # station logic sends the pilot back.
        return ["nobody at the helm"]
    if snapshot.beacon_hazards:
        return []  # do not linger here

    reasons = []
    medbay = player.systems.get("medbay")
    if medbay is not None and medbay.health > 0 and medbay.power > 0:
        # Only wait for crew who are actually being healed: in the medbay or on their way.
        hurt = [c.name for c in player.crew
                if c.ship_id == player.ship_id and not c.dead and not c.intruder and c.room_id >= 0
                and c.health < HEALED * c.max_health
                and (c.room_id == medbay.room_id or c.slot_room == medbay.room_id)]
        if hurt:
            reasons.append(f"healing {', '.join(hurt)}")
    damaged = [s.name for s in player.systems.values() if s.health < s.level]
    if damaged:
        reasons.append(f"repairing {', '.join(damaged)}")
    if any(room.fires or room.breaches for room in player.rooms):
        reasons.append("fires/breaches")
    return reasons


def _store_value(snapshot: GameSnapshot) -> float:
    """What visiting a store is worth now: repairs and fuel we need, or scrap to spend."""
    player = snapshot.player
    missing_hull = player.max_hull - player.hull
    value = min(missing_hull, player.scrap // 3) * 2.0
    if player.fuel < 5:
        value += 12
    if player.scrap >= 60:
        value += 8
    return value


BOSS_PREP_HULL = 0.6         # Last Stand: below this much hull, visit a store before the Flagship...
BOSS_PREP_SCRAP = 30         # ...when there is scrap for repairs
BOSS_PREP_MAX_JUMPS = 3      # ...and it is this close
BOSS_MAX_LOOKAHEAD = 16


def _boss_intercept(state: StarMapState, start: int, jumps_done: int, max_jumps: int):
    """Earliest walk from `start` that ends where the Flagship is when we arrive. The Flagship
    moves before we land (StarMap::GetNewLocation runs UpdateDangerZone -> UpdateBoss first),
    so arriving after jump j meets it at boss_after_jumps(j). Returns (jumps, path) or None."""
    layer = {start: [start]}
    for step in range(1, max_jumps + 1):
        nxt: dict[int, list[int]] = {}
        for address, path in layer.items():
            for other in state.beacons[address].connected:
                if other in state.beacons and other not in nxt:
                    nxt[other] = path + [other]
        target = state.boss_after_jumps(jumps_done + step)
        if target in nxt:
            return step, nxt[target][1:]
        layer = nxt
    return None


def choose_boss_route(state: StarMapState, snapshot: GameSnapshot, beacon_value) -> Plan | None:
    """The Last Stand: intercept the Flagship before it reaches the Federation base, with one
    detour to a store first when the hull needs repairs and the Flagship is still far off."""
    player = snapshot.player
    current = state.beacons.get(state.current)
    if current is None or state.boss_beacon is None:
        return None
    deadline = state.jumps_until_base()
    direct = _boss_intercept(state, current.address, 0, BOSS_MAX_LOOKAHEAD)
    notes = [f"Last Stand: the Flagship reaches the base in {deadline} jump(s)"
             f"{' (moving on our next jump)' if state.boss_jumping else ''}"]
    if player.hull < BOSS_PREP_HULL * player.max_hull and player.scrap >= BOSS_PREP_SCRAP:
        stores = [b for b in state.beacons.values() if b.store and b.address != current.address]
        best = None
        for store in stores:
            to_store = _boss_intercept_path(state, current.address, store.address, BOSS_PREP_MAX_JUMPS)
            if to_store is None:
                continue
            after = _boss_intercept(state, store.address, len(to_store), BOSS_MAX_LOOKAHEAD)
            if after is None or len(to_store) + after[0] > (deadline or 0) + 1:
                continue
            if best is None or len(to_store) + after[0] < best[0]:
                best = (len(to_store) + after[0], to_store)
        if best is not None:
            target = state.beacons[best[1][0]]
            return Plan(beacon=target, score=0.0, notes=notes + [
                f"hull {player.hull}/{player.max_hull}: repairs at a store first ({len(best[1])} jump(s)), "
                f"then the Flagship ({best[0]} jumps in all)"])
    if direct is None:
        return None
    jumps, path = direct
    target = state.beacons[path[0]]
    return Plan(beacon=target, score=0.0, notes=notes + [
        f"intercepting the Flagship in {jumps} jump(s)"
        + (" - next jump meets it" if jumps == 1 else "")])


def _boss_intercept_path(state: StarMapState, start: int, goal: int, max_jumps: int) -> list[int] | None:
    """Shortest path (beacon addresses after start) to goal, at most max_jumps long."""
    seen = {start: [start]}
    frontier = [start]
    for _ in range(max_jumps):
        nxt = []
        for address in frontier:
            for other in state.beacons[address].connected:
                if other in state.beacons and other not in seen:
                    seen[other] = seen[address] + [other]
                    if other == goal:
                        return seen[other][1:]
                    nxt.append(other)
        frontier = nxt
    return None


def choose_destination(state: StarMapState, snapshot: GameSnapshot, beacon_value) -> Plan | None:
    """beacon_value(beacon) -> (event value, has environment hazard) for an unvisited beacon."""
    player = snapshot.player
    current = state.beacons.get(state.current)
    if current is None:
        return None
    if state.boss_level:
        return choose_boss_route(state, snapshot, beacon_value)
    beacons = state.beacons
    to_exit = state.jumps_to_exit()
    exits = [b for b in beacons.values() if b.exit]
    store_value = _store_value(snapshot)

    cache: dict[int, tuple[float, bool]] = {}

    def gain(beacon: Beacon) -> float:
        if beacon.visited or beacon.overtaken:
            return 0.0
        if beacon.address not in cache:
            cache[beacon.address] = beacon_value(beacon)
        value, hazard = cache[beacon.address]
        value += store_value if beacon.store else 0.0
        return value - (HAZARD_PENALTY if hazard else 0.0)

    def fleet_x_after(moves: list[int]) -> float:
        return state.fleet[0] + sum(moves)

    def free_at(beacon: Beacon, fleet_x: float) -> bool:
        return math.hypot(beacon.x - fleet_x, beacon.y - state.fleet[1]) - state.fleet_radius > FLEET_SAFETY

    def exit_reachable(beacon: Beacon, jumps_done: int, fleet_x: float) -> bool:
        """Can we still get from `beacon` to an exit with the fuel and before the fleet takes it?"""
        remaining = to_exit.get(beacon.address)
        if remaining is None or jumps_done + remaining > player.fuel:
            return False
        worst_x = fleet_x + 64 * remaining  # assume normal advance for the rest of the way
        return any(free_at(e, worst_x) for e in exits)

    best: list = [float("-inf"), None]       # [value, path]
    expansions = 0

    on_path: set[int] = {current.address}

    def search(node: Beacon, path: list[Beacon], value: float, fleet_x: float):
        nonlocal expansions
        expansions += 1
        if node.exit and path:
            if value > best[0] or (value == best[0] and len(path) < len(best[1])):
                best[0], best[1] = value, list(path)
        if len(path) >= MAX_ROUTE_JUMPS or expansions > MAX_EXPANSIONS:
            return
        next_x = fleet_x + state.fleet_move(node.nebula)
        for address in node.connected:
            beacon = beacons.get(address)
            if beacon is None or address in on_path:
                continue
            if beacon.overtaken or not free_at(beacon, next_x):
                continue
            if not exit_reachable(beacon, len(path) + 1, next_x):
                continue
            step_gain = gain(beacon) * (DISCOUNT ** len(path))
            path.append(beacon)
            on_path.add(address)
            search(beacon, path, value + step_gain, next_x)
            on_path.discard(address)
            path.pop()

    search(current, [], 0.0, state.fleet[0])

    if current.exit:
        if best[1] is None or best[0] <= LEAVE_SECTOR_BONUS:
            return Plan(beacon=None, score=LEAVE_SECTOR_BONUS, leave_sector=True,
                        notes=["at the exit", "no route here is worth more than moving on"])

    if best[1] is None:
        # No safe route to the exit: run for it through whatever is closest to the exit.
        options = [beacons[a] for a in current.connected if a in beacons and a in to_exit]
        if not options:
            return None
        fallback = min(options, key=lambda b: (to_exit[b.address], b.overtaken))
        return Plan(beacon=fallback, score=0.0, notes=["no safe route; heading for the exit",
                                                       f"{to_exit[fallback.address]} jump(s) to exit"])

    route = best[1]
    steps = []
    for index, beacon in enumerate(route):
        g = gain(beacon)
        label = beacon.event_name or ("EXIT" if beacon.exit else "beacon")
        steps.append(f"{label}{'' if g == 0 else f' {g:+.0f}'}")
    notes = [f"route {' -> '.join(steps)}", f"total {best[0]:+.0f} in {len(route)} jump(s)"]
    return Plan(beacon=route[0], score=best[0], notes=notes)


def choose_sector(state: StarMapState, snapshot: GameSnapshot) -> int | None:
    if not state.sector_options:
        return None
    healthy = snapshot.player.hull >= 0.7 * snapshot.player.max_hull

    def score(option) -> float:
        kind = option.kind.upper()
        value = next((v for key, v in SECTOR_PREFERENCE.items() if key in kind), 0)
        if "HOSTILE" in kind and healthy:
            value += 1  # more fights, more scrap, when the ship can take it
        return value

    return max(state.sector_options, key=score).index
