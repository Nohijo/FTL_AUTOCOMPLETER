"""Weapons as a loadout: what a set of guns does together, which to mount and in what order,
what a store weapon adds, what to sell, and what to throw away when the hold is full.

A weapon's worth depends on the others. Shots from one volley strip shield layers for the
rest (a layer comes back about every 2 s, so only shots landing together count), ion locks
layers down for the next volley, beams lose a damage per layer still up, and missiles and
bombs ignore shields but cost ammunition. volley_value() simulates one volley of a weapon
set against the shields expected in this sector (strippers first, then damage dealers,
beams last, the way brain/live_combat.py fires them) and turns it into damage per second.

best_loadout() picks, from everything owned (mounted, cargo, the over-capacity box), the
weapons to mount: the powered set that fits the weapon system's power is chosen first and
mounted in the first slots (the bot powers weapons in slot order), spare weapons fill the
other slots. Values are in scrap, on the same scale as brain/shopper.py.
"""

from dataclasses import dataclass, field
from itertools import combinations

SCRAP_PER_DPS = 450.0          # a weapon that adds 0.2 damage per second through shields is worth ~90 scrap
EXPECTED_SHIELDS = {1: 1.0, 2: 1.0, 3: 1.5, 4: 2.0, 5: 2.0, 6: 2.5, 7: 3.0, 8: 3.5}   # layers by sector (Flagship: 4)
ACCURACY = 0.8                 # projectiles miss now and then (enemy dodge)
FLAK_ACCURACY = 0.7            # flak scatters over the target area
MISSILE_INTERCEPT = 0.85       # defense drones shoot some missiles down
SPARE_WEIGHT = 0.15            # a mounted but unpowered weapon: used once there is power for it
RESALE = 0.5                   # stores buy items back at half price


def kind(stats: dict) -> str:
    """laser / ion / flak / beam / missile / bomb / heal (heal bombs repair, never aim at enemies)."""
    t = stats.get("type", "")
    if t == "BEAM":
        return "beam"
    if t == "BOMB":
        return "heal" if stats.get("damage", 0) < 0 or "HEAL" in stats.get("name", "") else "bomb"
    if t == "MISSILES" or stats.get("missiles", 0) > 0:
        return "missile"
    if t == "BURST":
        return "flak"
    if stats.get("damage", 0) <= 0 and stats.get("ion", 0) > 0:
        return "ion"
    return "laser"


def _shots(stats: dict) -> int:
    if kind(stats) == "flak":
        return max(1, stats.get("mini_count", 0) or 3)
    return max(1, stats.get("shots", 1))


def _extras(stats: dict) -> float:
    """Fire, breaches, crew and system damage per shot that lands, in hull-damage equivalents."""
    return (0.04 * stats.get("fire", 0) + 0.04 * stats.get("breach", 0)
            + 0.15 * stats.get("crew_damage", 0) + 0.3 * stats.get("system_damage", 0)
            + (0.5 if stats.get("lockdown") else 0.0))


def volley_value(weapons: list[dict], layers: float, missiles_left: int = 99) -> float:
    """Scrap value (damage per second x SCRAP_PER_DPS) of these weapons fired as one volley
    against `layers` shield layers (fractional: blended between the two whole numbers)."""
    low, high = int(layers), int(layers) + 1
    frac = layers - low
    value = _volley(weapons, low, missiles_left)
    if frac > 0:
        value = (1 - frac) * value + frac * _volley(weapons, high, missiles_left)
    return value


def _volley(weapons: list[dict], layers: int, missiles_left: int) -> float:
    if not weapons:
        return 0.0
    projectile = [w for w in weapons if kind(w) in ("laser", "ion", "flak")]
    beams = [w for w in weapons if kind(w) == "beam"]
    pierce_all = [w for w in weapons if kind(w) in ("missile", "bomb")]
    cycle = max([w.get("cooldown", 10) for w in projectile + beams] or [10.0])
    # Ion locks layers between volleys when it comes back often enough; bombs and missiles
    # aimed at the shield room knock out bars (2 bars = 1 layer) until it is repaired.
    ion_lock = sum(w.get("ion", 0) for w in weapons if w.get("cooldown", 99) <= 10 and kind(w) in ("ion", "laser", "flak"))
    knocked = sum((w.get("system_damage", 0) + w.get("damage", 0) + w.get("ion", 0)) / 2.0
                  for w in pierce_all if w.get("cooldown", 99) <= cycle * 1.25)
    up = max(0, layers - ion_lock - int(knocked))

    # One volley: the cheapest shots go first and soak the layers, the rest hit.
    shots = []
    for w in projectile:
        accuracy = FLAK_ACCURACY if kind(w) == "flak" else ACCURACY
        for _ in range(_shots(w)):
            shots.append((w.get("damage", 0), w.get("pierce", 0), accuracy, w))
    shots.sort(key=lambda s: (s[0], -s[1]))
    damage = 0.0
    for dmg, pierce, accuracy, w in shots:
        if up > 0 and pierce < up:
            up -= 1                 # this shot takes a layer down
            continue
        damage += accuracy * (dmg + _extras(w))   # misses: the average shot lands `accuracy` of the time
    for w in beams:
        rooms = 1.0 + min(1.0, max(0, w.get("length", 0)) / 90.0)
        damage += max(0.0, w.get("damage", 0) - max(0.0, up - w.get("pierce", 0))) * rooms
        damage += 0.5 * _extras(w)
    per_second = damage / max(1.0, cycle)
    # Faster guns fire again between volleys: those extra volleys only hurt when they get
    # through the shields alone.
    for w in projectile:
        extra = cycle / max(1.0, w.get("cooldown", 10)) - 1
        if extra > 0:
            through = max(0, _shots(w) - layers) * ACCURACY * (w.get("damage", 0) + _extras(w))
            per_second += extra * through / cycle
    ammo = 1.0 if missiles_left >= 8 else 0.7 if missiles_left >= 3 else 0.3 if missiles_left >= 1 else 0.0
    for w in pierce_all:
        hit = w.get("damage", 0) + _extras(w)      # bombs: no hull damage (damage 0), systems and crew only
        if kind(w) == "missile":
            per_second += ammo * MISSILE_INTERCEPT * ACCURACY * _shots(w) * hit / max(1.0, w.get("cooldown", 10))
        else:
            per_second += ammo * _shots(w) * hit / max(1.0, w.get("cooldown", 10))
    return per_second * SCRAP_PER_DPS


def solo_value(stats: dict, layers: float, missiles_left: int = 99) -> float:
    return volley_value([stats], layers, missiles_left)


@dataclass
class Loadout:
    mounted: list[dict] = field(default_factory=list)   # in slot order; the first ones get power
    powered: int = 0                                    # how many of the first are powered
    value: float = 0.0
    notes: list[str] = field(default_factory=list)


def _powered_value(weapons: list[dict], power: int, layers: float, missiles: int) -> tuple[float, list[dict]]:
    """Best subset that fits the power, and its value."""
    best = (0.0, [])
    for size in range(len(weapons), 0, -1):
        for combo in combinations(weapons, size):
            if sum(max(1, w.get("power", 1)) for w in combo) > power:
                continue
            value = volley_value(list(combo), layers, missiles)
            if value > best[0]:
                best = (value, list(combo))
    return best


def best_loadout(weapons: list[dict], slots: int, power: int, sector: int, missiles: int) -> Loadout:
    """Which weapons to mount (at most `slots`) and in what order."""
    layers = EXPECTED_SHIELDS.get(max(1, min(8, sector)), 2.0)
    usable = list(weapons)
    value, powered = _powered_value(usable, max(0, power), layers, missiles)
    spares = sorted((w for w in usable if not any(w is p for p in powered)),
                    key=lambda w: -solo_value(w, layers, missiles))
    spares = spares[:max(0, slots - len(powered))]
    powered.sort(key=lambda w: -solo_value(w, layers, missiles))
    total = value + SPARE_WEIGHT * sum(solo_value(w, layers, missiles) for w in spares)
    return Loadout(mounted=powered + spares, powered=len(powered), value=total)


def current_value(mounted: list[dict], power: int, sector: int, missiles: int) -> float:
    """What the weapons do as mounted now: the bot powers them in slot order while power lasts."""
    layers = EXPECTED_SHIELDS.get(max(1, min(8, sector)), 2.0)
    powered, left = [], power
    for w in mounted:
        need = max(1, w.get("power", 1))
        if need <= left:
            powered.append(w)
            left -= need
    spares = [w for w in mounted if not any(w is p for p in powered)]
    return volley_value(powered, layers, missiles) + SPARE_WEIGHT * sum(solo_value(w, layers, missiles) for w in spares)


def purchase_gain(owned: list[dict], new: dict, slots: int, power: int, max_power: int,
                  sector: int, missiles: int) -> float:
    """Scrap value of adding `new` to what is owned: now, and once the weapon system has two
    more bars (the upgrader keeps adding weapon power)."""
    later = min(max_power, power + 2)
    now_gain = (best_loadout(owned + [new], slots, power, sector, missiles).value
                - best_loadout(owned, slots, power, sector, missiles).value)
    later_gain = (best_loadout(owned + [new], slots, later, sector, missiles).value
                  - best_loadout(owned, slots, later, sector, missiles).value)
    return max(0.0, 0.6 * now_gain + 0.4 * later_gain)


def keep_value(item: dict, item_kind: str, chosen: list[dict], usable_drone: bool) -> float:
    """What keeping an owned item is worth: being in the chosen loadout, or what a store pays."""
    if item_kind == "weapon" and any(item is w for w in chosen):
        return 1000.0 + item.get("cost", 0)
    if item_kind == "drone" and usable_drone:
        return 200.0 + item.get("cost", 0)
    return RESALE * item.get("cost", 0)


# --- the equipment screen: what to move where ---------------------------------------

REARRANGE_MIN_GAIN = 4.0       # scrap of loadout value a rearrangement must add (no churn)


def _weapon_boxes(equipment) -> tuple[list, list]:
    """(weapon slot boxes in slot order, boxes holding a weapon anywhere: slots, cargo, overflow)."""
    slots = [b for b in equipment.of("weapon") if not b.blocked]
    holding = [b for b in equipment.boxes if b.item_kind == "weapon" and b.kind in ("weapon", "cargo", "overflow")]
    return slots, holding


def plan_loadout(equipment, snapshot) -> tuple[Loadout, list, float]:
    """(best loadout over every owned weapon, the boxes its weapons sit in (slot order),
    value of the weapons as mounted now)."""
    player = snapshot.player
    weapons_system = player.systems.get("weapons")
    power = weapons_system.level if weapons_system is not None else 0
    sector, missiles = snapshot.sector or 1, player.missiles
    slots, holding = _weapon_boxes(equipment)
    stats = [b.stats for b in holding]
    best = best_loadout(stats, len(slots), power, sector, missiles)
    boxes = [next(b for b in holding if b.stats is w) for w in best.mounted]
    mounted_now = [b.stats for b in slots if b.item_kind == "weapon"]
    return best, boxes, current_value(mounted_now, power, sector, missiles)


def plan_equipment_move(equipment, snapshot) -> tuple | None:
    """The next drag on the equipment screen: (from box, to box, reason), or None when the
    loadout is as good as it gets. Run again after every drag (boxes swap their items)."""
    player = snapshot.player
    best, boxes, now_value = plan_loadout(equipment, snapshot)
    chosen = best.mounted
    usable_drones = "drones" in player.systems

    # 1. Something did not fit (a reward with a full hold): keep the most valuable items.
    overflow = equipment.overflow
    if overflow is not None and overflow.item_kind in ("weapon", "drone"):
        def keep(box) -> float:
            return keep_value(box.stats, box.item_kind, chosen, usable_drones)
        held = [b for b in equipment.boxes if b.kind == "cargo" and b.item_kind and not b.blocked]
        held += [b for b in equipment.of(overflow.item_kind) if b.item_kind and not b.blocked]
        if held:
            worst = min(held, key=keep)
            if keep(overflow) > keep(worst):
                return (overflow, worst, f"hold full: keeping {overflow.item} ({keep(overflow):.0f}) "
                                         f"over {worst.item} ({keep(worst):.0f}), which stays behind")

    # 2. Mount the best weapons, the ones to power first.
    slots, _ = _weapon_boxes(equipment)
    if best.value - now_value >= max(REARRANGE_MIN_GAIN, 0.04 * now_value):
        for index, box in enumerate(boxes):
            if index >= len(slots):
                break
            target = slots[index]
            if box.address != target.address:
                why = (f"mounting {box.item} in weapon slot {index + 1}"
                       + (f" (was {target.item})" if target.item else "")
                       + f": loadout {now_value:.0f} -> {best.value:.0f}")
                return box, target, why

    # 3. A drone in the cargo hold and an empty drone slot.
    if usable_drones:
        empty = [b for b in equipment.of("drone") if not b.item_kind and not b.blocked]
        spare = [b for b in equipment.boxes if b.kind == "cargo" and b.item_kind == "drone"]
        if empty and spare:
            return spare[0], empty[0], f"mounting drone {spare[0].item} from the cargo hold"
    return None


def sell_candidates(equipment, snapshot) -> list[tuple]:
    """Cargo items the ship has no use for: (box, scrap a store pays, reason)."""
    player = snapshot.player
    best, boxes, _ = plan_loadout(equipment, snapshot)
    keep = {b.address for b in boxes}
    out = []
    for box in equipment.boxes:
        if box.kind != "cargo" or not box.item_kind:
            continue
        price = int(RESALE * box.stats.get("cost", 0))
        if box.item_kind == "weapon" and box.address not in keep:
            out.append((box, price, f"{box.item} is not part of the best loadout"))
        elif box.item_kind == "drone" and "drones" not in player.systems:
            out.append((box, price, f"{box.item}: no Drone Control"))
    return out
