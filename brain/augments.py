"""What each augment is worth to the ship being flown, and how owned augments change play.

Effects, prices and stacking come from FTL's blueprints.xml (augBlueprint: cost, value,
stackable), read out of ftl.dat. A ship holds at most 3 augments; a non-stackable one owned
twice does nothing, and several only work with a system the ship may not have (Stealth
Weapons needs cloaking, Hacking Stun needs hacking, ...). augment_value() prices an augment
for this ship, the shopper and the event chooser use it; has() lets the brain adapt.
"""

from observer.memory.reader import ShipInfo

MAX_AUGMENTS = 3
STACKABLE = {"ION_ARMOR", "HULL_ARMOR", "ADV_HULL_ARMOR", "SCRAP_COLLECTOR", "AUTO_COOLDOWN", "SHIELD_RECHARGE",
             "FTL_BOOSTER", "STASIS_POD", "INTERNAL_AI", "EXTERNAL_AI"}
NEEDS_SYSTEM = {"CLOAK_FIRE": ("cloaking",), "HACKING_STUN": ("hacking",), "TELEPORT_HEAL": ("teleporter",),
                "BATTERY_BOOSTER": ("battery",), "BACKUP_DNA": ("clonebay",), "ZOLTAN_BYPASS": ("teleporter", "mind"),
                "DRONE_SPEED": ("drones",), "DRONE_RECOVERY": ("drones",)}


def has(ship: ShipInfo | None, name: str) -> bool:
    return ship is not None and ship.augments.get(name, 0) > 0


def augment_value(name: str, ship: ShipInfo, sector: int = 1, buying: bool = True) -> float:
    """Value in scrap of adding this augment to the ship now (0 = useless or redundant)."""
    owned = ship.augments
    if sum(owned.values()) >= MAX_AUGMENTS:
        return 0.0                               # no free slot (the store greys it out anyway)
    if owned.get(name) and name not in STACKABLE:
        return 0.0                               # a second copy does nothing
    needs = NEEDS_SYSTEM.get(name)
    if needs and not any(system in ship.systems for system in needs):
        return 0.0
    weapons = [w for w in ship.weapons]
    launchers = [w for w in weapons if w.missiles_per_shot > 0]
    drones = [d for d in ship.drones if not d.dead]
    fighting_drones = [d for d in drones if d.kind.upper() in ("COMBAT", "DEFENSE", "BOARDER", "SHIELD")]
    early = max(0.0, (8 - sector) / 7)           # 1.0 in sector 1, 0 in sector 8
    layers = ship.max_shield_layers
    stacked = owned.get(name, 0)
    value = {
        "WEAPON_PREIGNITE": 90 + 25 * len(weapons),
        "AUTO_COOLDOWN": 25 + 18 * len(weapons),
        "SCRAP_COLLECTOR": 20 + 90 * early,
        "SHIELD_RECHARGE": 30 + 12 * layers,
        "NANO_MEDBAY": 45 + (15 if "medbay" not in ship.systems else 0),
        "SYSTEM_CASING": 60,
        "ROCK_ARMOR": 55,
        "CRYSTAL_SHARDS": 30,
        "REPAIR_ARM": 25 + 20 * early,
        "FTL_BOOSTER": 30,
        "FTL_JAMMER": 25,
        "FLEET_DISTRACTION": 30 * early + 5,
        "DRONE_RECOVERY": 20 * len(fighting_drones),
        "DRONE_SPEED": 10 + 10 * len(fighting_drones),
        "DEFENSE_SCRAMBLER": 45 + 25 * len(launchers),
        "EXPLOSIVE_REPLICATOR": 25 * len(launchers),
        "CLOAK_FIRE": 55,
        "HACKING_STUN": 40,
        "ZOLTAN_BYPASS": 30,
        "TELEPORT_HEAL": 40,
        "BATTERY_BOOSTER": 25,
        "BACKUP_DNA": 30,
        "ION_ARMOR": 30,
        "O2_MASKS": 25,
        "FIRE_EXTINGUISHERS": 45,
        "SLUG_GEL": 30,
        "CREW_STIMS": 25,
        "LIFE_SCANNER": 10,
        "ADV_SCANNERS": 15 + 30 * early,         # Long-Ranged Scanners: see ships and stores before jumping
        "FTL_JUMPER": 10,
        "STASIS_POD": 15,                        # a quest item (can become a crew member)
        "ENERGY_SHIELD": 120,
    }.get(name, 25)
    if stacked:
        value *= 0.7 ** stacked                  # each extra copy helps a bit less
    return float(value)


def describe(ship: ShipInfo) -> list[str]:
    """How the owned augments change the bot's play (printed when they change)."""
    notes = []
    for name, rule in (("EXPLOSIVE_REPLICATOR", "missiles fired more freely (half are free)"),
                       ("DEFENSE_SCRAMBLER", "enemy defense drones ignored"),
                       ("NANO_MEDBAY", "crew heal anywhere: medbay trips only when badly hurt"),
                       ("O2_MASKS", "crew tolerate thinner air"),
                       ("FIRE_EXTINGUISHERS", "fewer crew sent to fires"),
                       ("DRONE_RECOVERY", "drones launched freely (parts come back)"),
                       ("REPAIR_ARM", "store repairs valued less"),
                       ("ZOLTAN_BYPASS", "boarding and mind control through Zoltan shields")):
        if has(ship, name):
            notes.append(rule)
    return notes
