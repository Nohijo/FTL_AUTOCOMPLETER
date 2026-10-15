"""What fights and follow-up events are worth, from FTL's own data.

Two verified sources:
  - data/events_db.json, built by tools/build_event_db.py from the event XML inside
    ftl.dat: every ship's destroyed / deadCrew / surrender outcome, event lists (one is
    picked at random), items, crew, status effects.
  - FTL's reward tables, read from FTL.amd64 (GetValue, called by GenerateReward):
    scrap = U(level range) * (15 + 6 * sector), with the sector counted from 0;
    fuel / missiles / drone parts drawn from per-level min..max tables.

The bot reads the outcome FTL already rolled for each choice from memory (exact), but a
fight's reward is only rolled when the fight ends; that is what this module estimates.
"""

import json
from functools import lru_cache

from config import PROJECT_DIR

DB_FILE = PROJECT_DIR / "data" / "events_db.json"

# GetValue tables (FTL.amd64 0x783400..0x783467), level LOW / MEDIUM / HIGH.
SCRAP_FACTOR = {"LOW": (0.5, 0.7), "MEDIUM": (0.8, 1.3), "HIGH": (1.3, 1.55)}
FUEL = {"LOW": (1, 3), "MEDIUM": (2, 4), "HIGH": (3, 6)}
MISSILES = {"LOW": (1, 2), "MEDIUM": (2, 4), "HIGH": (4, 8)}
DRONE_PARTS = {"LOW": (1, 1), "MEDIUM": (1, 1), "HIGH": (1, 2)}
ITEM_REWARD = 45.0             # an unknown weapon / drone / augment from a reward
CREW = 45.0


def _mean(pair) -> float:
    return (pair[0] + pair[1]) / 2


@lru_cache(maxsize=1)
def database() -> dict:
    try:
        return json.loads(DB_FILE.read_text())
    except (OSError, ValueError):
        return {"events": {}, "lists": {}, "ships": {}}


def auto_reward_value(reward: dict, sector: int, weights: dict) -> tuple[float, str]:
    """Expected value of an <autoReward level=...>type</autoReward>; sector counted from 1."""
    level = reward.get("level", "MEDIUM").replace("MED", "MEDIUM").replace("MEDIUMIUM", "MEDIUM")
    if level not in SCRAP_FACTOR:
        level = "MEDIUM"
    kind = reward.get("type", "standard")
    scrap = _mean(SCRAP_FACTOR[level]) * (15 + 6 * max(0, sector - 1))
    other = (_mean(FUEL[level]) * weights["fuel"] + _mean(MISSILES[level]) * weights["missiles"]
             + _mean(DRONE_PARTS[level]) * weights["drones"]) / 3
    if kind == "scrap_only":
        return scrap, f"~{scrap:.0f} scrap"
    if kind.endswith("_only"):
        table = {"fuel_only": (FUEL, "fuel"), "missiles_only": (MISSILES, "missiles"),
                 "droneparts_only": (DRONE_PARTS, "drones")}.get(kind)
        if table:
            amount = _mean(table[0][level])
            return amount * weights[table[1]], f"~{amount:.0f} {table[1]}"
    if kind == "stuff":
        return 2 * other, "supplies"
    if kind in ("weapon", "drone", "augment", "item"):
        return scrap * 0.5 + ITEM_REWARD, f"~{scrap * 0.5:.0f} scrap + an item"
    return scrap + other, f"~{scrap:.0f} scrap + supplies"     # standard


def db_outcome_value(outcome: dict, sector: int, weights: dict, depth: int = 0) -> tuple[float, list[str]]:
    """Expected value of an outcome from the event database (follows loads and lists)."""
    db = database()
    notes: list[str] = []
    if depth > 4 or not outcome:
        return 0.0, notes
    if "load" in outcome:
        name = outcome["load"]
        if name in db["lists"]:
            options = [db_outcome_value(o, sector, weights, depth + 1) for o in db["lists"][name]]
            if options:
                value = sum(v for v, _ in options) / len(options)
                return value, [f"one of {len(options)} outcomes (avg {value:+.0f})"]
        if name in db["events"]:
            return db_outcome_value(db["events"][name], sector, weights, depth + 1)
    value = 0.0
    if "auto_reward" in outcome:
        amount, note = auto_reward_value(outcome["auto_reward"], sector, weights)
        value += amount
        notes.append(note)
    for kind, amount in outcome.get("items", {}).items():
        weight = {"scrap": 1.0, "fuel": weights["fuel"], "missiles": weights["missiles"],
                  "drones": weights["drones"]}.get(kind, 1.0)
        value += amount * weight
        notes.append(f"{amount:+.0f} {kind}")
    for gear in outcome.get("gear", []):
        value += ITEM_REWARD
        notes.append(f"+{gear.get('name') or gear['kind']}")
    if outcome.get("crew"):
        value += CREW * outcome["crew"]
        notes.append(f"+{outcome['crew']} crew")
    for damage in outcome.get("damage", []):
        if damage.get("amount", 0) < 0:
            value += -damage["amount"] * 2.0
            notes.append(f"repair {-damage['amount']} hull")
    if outcome.get("choices"):
        best = max((db_outcome_value(c.get("event", {}), sector, weights, depth + 1) for c in outcome["choices"]),
                   key=lambda option: option[0])
        value += max(0.0, best[0])
        notes += best[1]
    return value, notes


def fight_value(ship_name: str, sector: int, weights: dict) -> tuple[float, str] | None:
    """Expected reward for beating this ship (destroying it; killing the crew noted when it
    pays more). None when the ship is not in the database."""
    ship = database()["ships"].get(ship_name)
    if not ship:
        return None
    destroyed, notes = db_outcome_value(ship.get("destroyed", {}), sector, weights)
    dead_crew, crew_notes = db_outcome_value(ship.get("deadCrew", {}), sector, weights)
    text = ", ".join(notes) or "nothing"
    if dead_crew > destroyed + 10:
        text += f"; killing the crew pays {dead_crew:.0f} ({', '.join(crew_notes)})"
    return destroyed, text


def default_fight_value(sector: int, weights: dict) -> tuple[float, str]:
    """A typical fight (MEDIUM standard reward) when the ship is unknown."""
    return auto_reward_value({"level": "MEDIUM", "type": "standard"}, sector, weights)
