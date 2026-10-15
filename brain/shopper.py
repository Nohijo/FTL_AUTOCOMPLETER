"""Decide what to buy at a store, one purchase at a time: a triage of everything for sale.

Each candidate gets a value in scrap: what it is worth to this run, given the
ship as it is now. Survival comes first (hull under half, almost no fuel).
After that the bot buys whatever is worth the most more than it costs (value
at least VALUE_PER_SCRAP times the price), keeping a small reserve unless the
item is a survival need, and asks again after every purchase, so crew, a gun
and some fuel can all come out of one visit.

The values are a build plan in numbers:
  - crew matter most when there are few of them (stations, repairs, boarders)
  - weapons are rated by what they add to the weapons already owned (mounted or in the
    cargo hold), fired together as one volley against the shields of this sector
    (brain/arsenal.py); one bought with every slot full goes to the cargo hold and the
    equipment pilot mounts it in place of a weaker gun, which is then sold
  - drones need Drone Control and a free drone slot; Drone Control itself is
    only worth buying together with a good drone from the same store
  - systems from a table; augments by their synergy with this ship (brain/augments.py)
  - fuel, missiles, drone parts and hull repairs are worth more the lower they are
"""

from brain import arsenal
from brain.augments import augment_value, has
from observer.memory.reader import GameSnapshot
from observer.memory.store import StoreItem, StoreState

VALUE_PER_SCRAP = 1.0          # buy only what is worth at least its price
RESERVE = 12                   # scrap kept back for fuel and repairs when buying non-essentials...
RESERVE_HEALTHY = 5            # ...or this much when fuel and hull are fine

CREW_VALUE = {1: 160, 2: 140, 3: 95, 4: 60, 5: 45, 6: 35, 7: 25}   # by crew already aboard
SPECIES_BONUS = {"engi": 10, "mantis": 10, "rock": 10, "slug": 5, "zoltan": 5, "crystal": 10}

SYSTEM_VALUE = {               # buying a system the ship does not have
    "cloaking": 160, "hacking": 120, "battery": 55, "teleporter": 45, "mind": 45,
    "medbay": 160, "shields": 200, "engines": 200, "oxygen": 200, "weapons": 200,
    "clonebay": 0,             # would replace the medbay
}
DRONE_VALUE = {                # by DroneBlueprint.typeName
    "DEFENSE": 95, "COMBAT": 100, "SHIP_REPAIR": 45, "BATTLE": 40, "REPAIR": 30,
    "BOARDER": 30, "HACKING": 0, "SHIELD": 50,
}
WEAPON_SCALE = 260             # scrap per (damage-equivalent per second)
UNAIMABLE_WEAPONS: set[str] = set()   # brain/live_combat.UNSUPPORTED_WEAPON_TYPES


def weapon_value(stats: dict) -> float:
    """Damage per second, counting what helps get through shields: every shot strips a
    layer (worth half a damage), ion shots strip and lock layers, missiles ignore shields
    but cost ammunition."""
    if not stats or stats.get("type") in UNAIMABLE_WEAPONS:
        return 0.0
    shots, damage, ion = max(1, stats["shots"]), stats["damage"], stats["ion"]
    cooldown = max(1.0, stats["cooldown"])
    if stats["missiles"] > 0:
        per_volley = shots * damage * 1.3 * 0.8          # bypasses shields, uses missiles
    else:
        per_volley = shots * damage + 0.5 * shots + 1.0 * shots * ion
    return WEAPON_SCALE * per_volley / cooldown


def _supply_values(store: StoreState, snapshot: GameSnapshot) -> list[tuple[StoreItem, float, str, bool]]:
    """(item, value of one more unit, reason, survival need) for fuel, ammo and repairs."""
    player = snapshot.player
    hull = player.hull / max(1, player.max_hull)
    has_launcher = any(w.missiles_per_shot > 0 for w in player.weapons)
    has_drone_bay = "drones" in player.systems
    last_stand = (snapshot.sector or 0) >= 8     # the Flagship is next: scrap is worth nothing after it
    out = []
    for item in store.items:
        name = item.name.lower()
        if item.kind == "repair" and not item.repair_all and player.hull < player.max_hull:
            per_point = 6 if hull < 0.5 else 3.5 if hull < 0.75 else 2.2 if hull < 0.9 else 1.2
            if has(player, "REPAIR_ARM") and hull >= 0.5:
                per_point *= 0.7         # the Repair Arm mends hull as scrap comes in
            if last_stand:
                per_point = max(per_point, 4.0)
            out.append((item, per_point * max(1, item.cost) / 2, f"hull {player.hull}/{player.max_hull}",
                        hull < (0.8 if last_stand else 0.5)))
        elif item.kind == "item" and "fuel" in name:
            value = 10 if player.fuel < 4 else 6 if player.fuel < 8 else 3.5 if player.fuel < 12 else 0
            out.append((item, value, f"fuel {player.fuel}", player.fuel < 4))
        elif item.kind == "item" and "missile" in name and has_launcher:
            value = 14 if player.missiles < 3 else 8 if player.missiles < 6 else 0
            if last_stand:
                value = 12 if player.missiles < 10 else 4     # three Flagship stages to shoot through
            out.append((item, value, f"missiles {player.missiles}", False))
        elif item.kind == "item" and "drone" in name and has_drone_bay:
            value = 14 if player.drone_parts < 3 else 9 if player.drone_parts < 6 else 0
            out.append((item, value, f"drone parts {player.drone_parts}", False))
    return out


def owned_weapons(snapshot: GameSnapshot, equipment=None) -> list[dict]:
    """Stats of every weapon the ship has: mounted, and (with the equipment read) in cargo."""
    weapons = [w.stats for w in snapshot.player.weapons if w.stats]
    if equipment is not None:
        weapons += [b.stats for b in equipment.boxes if b.kind in ("cargo", "overflow") and b.item_kind == "weapon"]
    return weapons


def room_for(equipment, kind: str, store: StoreState) -> bool:
    """A bought weapon / drone needs a free slot of its kind or a free cargo box."""
    if equipment is None:
        return (store.weapons_held < store.weapon_slots) if kind == "weapon" else (store.drones_held < store.drone_slots)
    free = [b for b in equipment.boxes if b.kind in (kind, "cargo") and not b.item_kind and not b.blocked]
    return bool(free)


def _build_values(store: StoreState, snapshot: GameSnapshot, equipment=None) -> list[tuple[StoreItem, float, str]]:
    """(item, value, reason) for crew, weapons, drones, systems and augments."""
    player = snapshot.player
    crew = [c for c in player.crew if c.ship_id == player.ship_id and not c.dead and not c.intruder
            and "drone" not in f"{c.name} {c.species}".lower()]
    best_drone = max((DRONE_VALUE.get(i.stats.get("type", ""), 0) for i in store.items
                      if i.kind == "drone" and i.count != 0), default=0)
    out = []
    for item in store.items:
        name = item.name
        if item.kind == "crew":
            value = CREW_VALUE.get(len(crew), 0) + SPECIES_BONUS.get(name.lower(), 0)
            out.append((item, value, f"crew {len(crew)} -> {len(crew) + 1} ({name})"))
        elif item.kind == "weapon":
            weapons = player.systems.get("weapons")
            if weapons is None or not item.stats or not room_for(equipment, "weapon", store):
                continue
            slots = store.weapon_slots or 4
            value = arsenal.purchase_gain(owned_weapons(snapshot, equipment), item.stats, slots, weapons.level,
                                          weapons.max_level, snapshot.sector or 1, player.missiles)
            if value:
                out.append((item, value, f"weapon {name} ({arsenal.kind(item.stats)}, {item.stats.get('shots')}x"
                                         f"{item.stats.get('damage')} every {item.stats.get('cooldown', 0):.0f}s, "
                                         f"{item.stats.get('power')} power): adds {value:.0f} to the loadout"))
        elif item.kind == "drone":
            if "drones" not in player.systems or not room_for(equipment, "drone", store):
                continue
            value = DRONE_VALUE.get(item.stats.get("type", ""), 20)
            out.append((item, value, f"drone {name}"))
        elif item.kind == "system":
            key = name.lower()
            if key in player.systems:
                continue
            if key == "drones":
                # Only worth it with a good drone to put in it, bought next from this store, so
                # only when the scrap covers both (seen: Drone Control bought with 2 scrap left
                # and the Defense Drone beside it unaffordable: 85 scrap wasted).
                drone_costs = [i.cost for i in store.items if i.kind == "drone" and i.count != 0
                               and DRONE_VALUE.get(i.stats.get("type", ""), 0) >= 80]
                affordable = drone_costs and player.scrap >= item.cost + min(drone_costs)
                value = best_drone * 0.9 if best_drone >= 80 and affordable else 0
            else:
                value = SYSTEM_VALUE.get(key, 30)
            out.append((item, value, f"system {name}"))
        elif item.kind == "augment":
            value = augment_value(name, player, snapshot.sector or 1)
            if value:
                out.append((item, value, f"augment {name}"))
    return out


def next_purchase(store: StoreState, snapshot: GameSnapshot, equipment=None) -> tuple[StoreItem, str] | None:
    player = snapshot.player
    scrap = player.scrap

    def buyable(item: StoreItem, budget: int) -> bool:
        return item.active and item.count != 0 and 0 < item.cost <= budget

    supplies = _supply_values(store, snapshot)
    # 1. Survival: hull under half, fuel under 4.
    for item, value, reason, urgent in supplies:
        if urgent and buyable(item, scrap):
            return item, f"{reason} (urgent)"
    # 2. Everything else, best value for the scrap first, keeping a reserve unless the
    #    supplies are healthy.
    reserve = RESERVE_HEALTHY if (player.fuel >= 6 and player.hull >= 0.6 * player.max_hull) else RESERVE
    last_stand = (snapshot.sector or 0) >= 8
    if last_stand:
        reserve = 0                      # nothing to save scrap for after the Flagship
    candidates = [(item, value, reason) for item, value, reason, _ in supplies if buyable(item, scrap)]
    candidates += [(item, value, reason) for item, value, reason in _build_values(store, snapshot, equipment)
                   if buyable(item, scrap - reserve)]
    bar = VALUE_PER_SCRAP * (0.5 if last_stand else 1.0)
    worth = [(value / item.cost, value - item.cost, item, reason) for item, value, reason in candidates
             if value >= bar * item.cost]
    if not worth:
        return None
    # Big upgrades to the run first (most value beyond the price), then the best ratio.
    worth.sort(key=lambda w: (w[1], w[0]), reverse=True)
    _, gain, item, reason = worth[0]
    return item, f"{reason}; worth ~{gain + item.cost:.0f} scrap"


def appraise(store: StoreState, snapshot: GameSnapshot, equipment=None) -> list[str]:
    """Every item on every store page with its value to this ship (for the log)."""
    values = {id(item): (value, reason) for item, value, reason in _build_values(store, snapshot, equipment)}
    values.update({id(item): (value, reason) for item, value, reason, _ in _supply_values(store, snapshot)})
    lines = []
    for page in (1, 2, 0):
        items = [i for i in store.items if i.page == page and i.count != 0]
        if not items:
            continue
        label = {1: "page 1", 2: "page 2", 0: "supplies"}[page]
        parts = []
        for item in items:
            value, _ = values.get(id(item), (0.0, ""))
            parts.append(f"{item.name} {item.cost}s (worth {value:.0f})")
        lines.append(f"{label}: " + ", ".join(parts))
    return lines
