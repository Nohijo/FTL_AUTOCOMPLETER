"""Decide which ship upgrade to buy next.

Prices come from the game (SystemBlueprint.upgradeCosts, read in
observer/memory/upgrades.py). The order follows common FTL practice: a second
shield layer first, enough reactor to run what the ship has, dodge chance,
then power for every weapon, then the rest.
"""

from brain.ship_profile import adapt_upgrade_steps
from observer.memory.reader import ShipInfo
from observer.memory.upgrades import UpgradeOption, UpgradesState

REACTOR_CAP = 25


def scrap_reserve(ship: ShipInfo) -> int:
    """Scrap kept back for hull repairs and fuel at the next store."""
    return 15 + (10 if ship.fuel < 5 else 0) + min(30, 2 * (ship.max_hull - ship.hull))


def reactor_cost(reactor_total: int, pending: int) -> int:
    # ReactorButton::GetReactorCost: 30 up to 4 bars, then 15 + 5 * (bars // 5).  REA (estimate)
    bars = reactor_total + pending
    return 30 if bars <= 4 else 15 + 5 * (bars // 5)


def _weapons_power(ship: ShipInfo) -> int:
    return sum(w.required_power for w in ship.weapons)


def _power_wanted(ship: ShipInfo, levels: dict[str, int]) -> int:
    wanted = 0
    for name in ("shields", "engines", "oxygen", "medbay", "drones", "cloaking", "teleporter", "hacking", "mind"):
        if name in levels:
            wanted += 1 if name in ("oxygen", "medbay") else levels[name]
    if "weapons" in levels:
        wanted += min(levels["weapons"], _weapons_power(ship))
    return wanted


def plan_upgrade(state: UpgradesState, ship: ShipInfo, profile=None) -> tuple[str, UpgradeOption | None, int, str] | None:
    """(what, box or None for the reactor, cost, reason) for the next upgrade, or None."""
    by_name = {o.system: o for o in state.options}
    levels = {name: o.level for name, o in by_name.items()}
    budget = ship.scrap - scrap_reserve(ship)
    reactor_total = (ship.reactor_total or 0) + state.reactor_pending

    steps = [
        ("shields", 4, "a second shield layer"),
        ("reactor", None, "power for the systems"),
        ("engines", 4, "dodge chance"),
        ("weapons", max(1, _weapons_power(ship)), "power for every weapon"),
        ("pilot", 2, "dodge chance"),
        ("shields", 6, "a third shield layer"),
        ("engines", 5, "dodge chance"),
        ("doors", 2, "slow fires and boarders"),
        ("medbay", 2, "faster healing"),
        ("oxygen", 2, "faster oxygen refill"),
        ("sensors", 2, "see the enemy ship"),
        ("weapons", 6, "room for stronger weapons"),
        ("pilot", 3, "dodge chance"),
        # Cloaking level 2 is a must for the Flagship (strategy reference, section 10), so it
        # comes before the fourth layer.
        ("cloaking", 2, "a cloak long enough for the Flagship's volleys"),
        ("shields", 8, "a fourth shield layer"),
        # Late game: keep turning scrap into strength (the list used to end here, so scrap
        # piled up unused once the shields were maxed).
        ("engines", 6, "dodge chance"),
        ("weapons", 7, "room for stronger weapons"),
        ("medbay", 3, "faster healing"),
        ("engines", 7, "dodge chance"),
        ("weapons", 8, "room for stronger weapons"),
        ("hacking", 2, "stronger hacking"),
        ("cloaking", 3, "longer cloak"),
        ("drones", 6, "power for more drones"),
        ("engines", 8, "dodge chance"),
        ("doors", 3, "stronger doors"),
        ("oxygen", 3, "faster oxygen refill"),
        ("sensors", 3, "see enemy weapons charging"),
        ("teleporter", 2, "faster teleporter"),
        ("mind", 2, "longer mind control"),
        ("hacking", 3, "stronger hacking"),
        ("battery", 2, "more battery power"),
        ("clonebay", 2, "faster cloning"),
        ("drones", 8, "power for more drones"),
        ("teleporter", 3, "faster teleporter"),
        ("mind", 3, "longer mind control"),
        ("reactor", None, "spare power"),
    ]
    # This ship's loadout and its past runs reorder the plan (brain/ship_profile.py).
    steps = adapt_upgrade_steps(steps, profile, levels, ship)
    for name, target, reason in steps:
        if name == "reactor":
            wanted = REACTOR_CAP if reason == "spare power" else min(REACTOR_CAP, _power_wanted(ship, levels))
            if reactor_total >= wanted:
                continue
            cost = reactor_cost(ship.reactor_total or 0, state.reactor_pending)
            if cost <= budget:
                return "reactor", None, cost, f"{reason} ({reactor_total} bars, want {_power_wanted(ship, levels)})"
            return None
        option = by_name.get(name)
        if option is None or option.level >= target or option.next_cost is None:
            continue
        if option.next_cost <= budget:
            return name, option, option.next_cost, f"{name} {option.level} -> {option.level + 1}: {reason}"
        return None  # save up for this one instead of buying something less important
    return None
