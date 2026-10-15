"""Pick the best choice of the event on screen by scoring what each choice leads to.

Outcomes are read from memory (observer/memory/events.py), so the score is
based on what the game will actually do, not on the choice text. Weights are
rough scrap-equivalents and can be tuned below.
"""

from dataclasses import dataclass

from brain.augments import augment_value
from brain.event_knowledge import default_fight_value, fight_value
from brain.shopper import SYSTEM_VALUE
from observer.memory import layout as L
from observer.memory.events import Choice, EventOnScreen, Outcome, Resources
from observer.memory.reader import ShipInfo

SCRAP = 1.0
FUEL, MISSILE, DRONE_PART = 3.0, 3.0, 3.0
LOW_FUEL, LOW_MISSILES = 3, 2             # below these, those resources are worth more
SCARCE_MULTIPLIER = 3.0
STRANDED_FUEL = 40.0                      # each of the first fuel units when the tank is (almost) empty
CREW = 45.0
ITEM_VALUE_OF_COST = 0.6                  # weapons / drones / augments: 60% of store price
ITEM_UNKNOWN_COST = 40.0
CARGO_VALUE_OF_COST = 0.3                 # unusable gear: roughly what it sells for, minus the cargo space
HULL_POINT = 3.0
HULL_POINT_WHEN_LOW = 7.0                 # below 40% hull
SYSTEM_DAMAGE_POINT = 5.0
BOARDER = 7.0
INTRUDERS = 15.0
FIGHT_RISK = 12.0
FIGHT_WIN_ODDS = 0.8
# FTL rolls a fight's reward only when the enemy is destroyed, so it is usually not in
# memory beforehand. Estimate it from the sector instead.
FIGHT_SCRAP_BASE, FIGHT_SCRAP_PER_SECTOR = 15.0, 5.0
STORE = 8.0
LOW_HULL_FIGHT = 30.0                     # extra risk of an optional fight below 30% hull
HAZARD_FIGHT = 8.0                        # extra risk of fighting in a hazard
LAST_STAND_FIGHT = 100.0                  # sector 8: any fight but the Flagship's costs hull and time the chase needs
REPAIR_PER_HULL = 2.0

# Status effects (e.g. "engines and shields halved"): value of one bar of a system, mostly
# during a fight at the same beacon.
SYSTEM_BAR = {"shields": 9.0, "engines": 5.0, "weapons": 6.0, "pilot": 4.0, "oxygen": 3.0, "drones": 4.0,
              "medbay": 2.0, "cloaking": 4.0, "hacking": 3.0, "mind": 3.0, "teleporter": 3.0}
STATUS_WITHOUT_FIGHT = 0.3
UPGRADE_BAR = 25.0                        # a free system upgrade (or reactor bar)
PURSUIT_JUMP = 8.0                        # the rebel fleet one jump closer
REVEAL_MAP = 4.0
QUEST = 12.0
UNLOCK_SHIP = 5.0
BLUE_BONUS = 5.0                          # blue options are designed to be the good ones

UNAFFORDABLE = float("-inf")


def _own_crew(ship: ShipInfo) -> int:
    return sum(1 for c in ship.crew if c.ship_id == ship.ship_id and not c.dead and not c.intruder
               and c.room_id >= 0 and "drone" not in f"{c.name} {c.species}".lower())


def _fight_ahead(outcome: Outcome | None, depth: int = 0) -> bool:
    if outcome is None or depth > 3:
        return False
    return outcome.hostile_ship or any(_fight_ahead(c.outcome, depth + 1) for c in outcome.choices)


def _status_value(outcome: Outcome, ship: ShipInfo, notes: list[str]) -> float:
    """Status effects at this beacon: harmful on our ship, helpful on the enemy's."""
    weight_scale = 1.0 if _fight_ahead(outcome) else STATUS_WITHOUT_FIGHT
    value = 0.0
    for kind, system, amount, target in outcome.status_effects:
        if kind == L.STATUS_CLEAR:
            continue
        name = L.SYSTEM_NAMES.get(system, f"system {system}")
        mine = ship.systems.get(name)
        power = (mine.power if mine is not None else 0) if target == 0 else 2
        if kind == L.STATUS_LOSS:
            lost = min(power, amount)
        elif kind == L.STATUS_LIMIT:
            lost = max(0, power - amount)
        else:                              # divide
            lost = power - power // max(1, amount)
        if not lost:
            continue
        worth = lost * SYSTEM_BAR.get(name, 2.0) * weight_scale
        if target == 0:
            value -= worth
            notes.append(f"our {name} -{lost} bar(s)")
        else:
            value += 0.7 * worth
            notes.append(f"enemy {name} -{lost} bar(s)")
    return value


@dataclass
class ScoredChoice:
    index: int
    text: str
    score: float
    summary: str
    selectable: bool


def _items(res: Resources, ship: ShipInfo | None = None) -> list[tuple[str, float]]:
    found = []
    for kind, name, cost in (("weapon", res.weapon, res.weapon_cost), ("drone", res.drone, res.drone_cost)):
        if not name:
            continue
        value = cost * ITEM_VALUE_OF_COST if cost > 0 else ITEM_UNKNOWN_COST
        # Gear the ship cannot use only sits in the cargo hold (4 slots) until sold for
        # about half its price: a drone without Drone Control, a weapon with every slot full.
        unusable = ship is not None and ((kind == "drone" and "drones" not in ship.systems)
                                         or (kind == "weapon" and len(ship.weapons) >= 4))
        if unusable:
            value = (cost if cost > 0 else ITEM_UNKNOWN_COST) * CARGO_VALUE_OF_COST
        found.append((name, value))
    if res.augment:
        # Augments by what they do for this ship (brain/augments.py): nothing when redundant
        # or when all three slots are full.
        found.append((res.augment, augment_value(res.augment, ship) if ship is not None
                      else res.augment_cost * ITEM_VALUE_OF_COST))
    return found


MAX_CREW = 8


def _crew_value(ship: ShipInfo, change: int) -> float:
    """A crew member is worth CREW while there is room; with every place taken a new one only
    forces someone out (FTL blocks the ship screen until one is dismissed)."""
    if change > 0 and _own_crew(ship) >= MAX_CREW:
        return 0.0
    return CREW


def _resources_value(res: Resources, ship: ShipInfo, notes: list[str], label: str = "") -> float:
    fuel_weight = FUEL * (SCARCE_MULTIPLIER if ship.fuel <= LOW_FUEL else 1)
    missile_weight = MISSILE * (SCARCE_MULTIPLIER if ship.missiles <= LOW_MISSILES else 1)
    hull_weight = HULL_POINT_WHEN_LOW if ship.hull < 0.4 * ship.max_hull else HULL_POINT

    fuel_value = res.fuel * fuel_weight
    if res.fuel > 0 and ship.fuel <= 1:
        # Out of fuel the ship is stranded until the rebel fleet arrives (seen: a run lost after
        # turning down "15 scrap for 5 fuel"). The first jumps' worth of fuel is worth nearly anything.
        first = min(res.fuel, 3 - ship.fuel)
        fuel_value = first * STRANDED_FUEL + (res.fuel - first) * fuel_weight
    value = (res.scrap * SCRAP + fuel_value + res.missiles * missile_weight
             + res.drones * DRONE_PART + res.crew * _crew_value(ship, res.crew) - res.hull_damage * hull_weight)
    for name, amount in (("scrap", res.scrap), ("fuel", res.fuel), ("missiles", res.missiles),
                         ("drone parts", res.drones), ("crew", res.crew)):
        if amount:
            notes.append(f"{label}{amount:+d} {name}")
    if res.hull_damage:
        notes.append(f"{label}-{res.hull_damage} hull")
    for name, item_value in _items(res, ship):
        value += item_value
        notes.append(f"{label}+{name}")
    if res.system_id >= 0:
        name = L.SYSTEM_NAMES.get(res.system_id, f"system {res.system_id}")
        gain = SYSTEM_VALUE.get(name, 40) if name not in ship.systems else 10
        value += gain
        notes.append(f"{label}+{name} system")
    if res.upgrade_amount > 0:
        value += UPGRADE_BAR * res.upgrade_amount
        notes.append(f"{label}+{res.upgrade_amount} upgrade")
    if res.intruders:
        value -= INTRUDERS
        notes.append(f"{label}intruders")
    return value


def _affordable(res: Resources, ship: ShipInfo) -> bool:
    return (ship.scrap + res.scrap >= 0 and ship.fuel + res.fuel >= 0
            and ship.missiles + res.missiles >= 0 and ship.drone_parts + res.drones >= 0)


def outcome_value(outcome: Outcome | None, ship: ShipInfo, notes: list[str], sector: int = 1) -> float:
    if outcome is None:
        return 0.0
    if not _affordable(outcome.stuff, ship):
        notes.append("cannot afford")
        return UNAFFORDABLE

    if outcome.stuff.crew < 0 and _own_crew(ship) + outcome.stuff.crew <= 0:
        notes.append("would lose the last crew member")
        return UNAFFORDABLE
    if outcome.stuff.hull_damage >= ship.hull:
        notes.append("the hull damage would destroy the ship")
        return UNAFFORDABLE
    value = _resources_value(outcome.stuff, ship, notes)
    value += _status_value(outcome, ship, notes)
    if outcome.modify_pursuit:
        value -= PURSUIT_JUMP * outcome.modify_pursuit
        notes.append(f"rebel fleet {outcome.modify_pursuit:+d} jump(s)")
    if outcome.reveal_map:
        value += REVEAL_MAP
        notes.append("map revealed")
    if outcome.quest:
        value += QUEST
        notes.append("quest beacon")
    if outcome.unlock_ship >= 0:
        value += UNLOCK_SHIP
        notes.append("unlocks a ship")
    if outcome.hostile_ship:
        risk = FIGHT_RISK * (2.0 if ship.hull < 0.4 * ship.max_hull else 1.0)
        # Most lost runs ended in a fight entered with little hull (data/runs post-mortems):
        # make optional fights costlier as the hull drops.
        if ship.hull < 0.3 * ship.max_hull:
            risk += LOW_HULL_FIGHT       # an optional fight now can end the run
        elif ship.hull < 0.5 * ship.max_hull:
            risk += LOW_HULL_FIGHT / 2
        if outcome.environment:
            risk += HAZARD_FIGHT         # fighting next to a sun, in asteroids, a storm...
        if sector >= 8 and not (outcome.ship_name or "").startswith("BOSS"):
            risk += LAST_STAND_FIGHT     # the Last Stand: follow the Flagship, no side fights
            notes.append("side fight in the Last Stand")
        reward_notes: list[str] = []
        reward = _resources_value(outcome.reward, ship, reward_notes)
        if not reward_notes:
            # FTL rolls a fight's reward when it ends: take the ship's destroyed outcome from
            # FTL's event data (brain/event_knowledge.py), or a typical MEDIUM reward.
            weights = {"fuel": FUEL * (SCARCE_MULTIPLIER if ship.fuel <= LOW_FUEL else 1),
                       "missiles": MISSILE * (SCARCE_MULTIPLIER if ship.missiles <= LOW_MISSILES else 1),
                       "drones": DRONE_PART}
            known = fight_value(outcome.ship_name, sector, weights) if outcome.ship_name else None
            reward, note = known if known else default_fight_value(sector, weights)
            reward_notes.append(note)
        value += FIGHT_WIN_ODDS * reward - risk
        notes.append(f"FIGHT (win: {', '.join(reward_notes)})")
    else:
        value += _resources_value(outcome.reward, ship, notes)
    if outcome.system_damage:
        value -= SYSTEM_DAMAGE_POINT * sum(amount for _, amount in outcome.system_damage)
        notes.append("system damage")
    if outcome.boarders:
        race, low, high = outcome.boarders
        value -= BOARDER * (low + high) / 2
        notes.append(f"{low}-{high} {race or 'boarders'} board")
    if outcome.store:
        value += STORE
        notes.append("store")
    if outcome.repair:
        value += REPAIR_PER_HULL * (ship.max_hull - ship.hull)
        notes.append("hull repair")

    # Follow-up choices: assume the best one will be taken.
    if outcome.choices:
        options = []
        for child in outcome.choices:
            child_notes: list[str] = []
            options.append((outcome_value(child.outcome, ship, child_notes, sector), child_notes))
        best_value, best_notes = max(options, key=lambda option: option[0])
        if best_value != UNAFFORDABLE:
            value += best_value
            notes.extend(best_notes)
    return value


def score_event(event: EventOnScreen, ship: ShipInfo, sector: int = 1) -> list[ScoredChoice]:
    # ChoiceText.type 1 is assumed to mean "requirement not met" (greyed out).  RUNTIME
    aligned = len(event.shown_types) == len(event.choices)
    scored = []
    own_crew = _own_crew(ship)
    virus = "virus" in event.text.lower()
    for choice in event.choices:
        notes: list[str] = []
        score = outcome_value(choice.outcome, ship, notes, sector)
        outcome = choice.outcome
        if choice.blue and score != UNAFFORDABLE:
            notes.insert(0, f"blue option ({choice.requirement})")
            score += BLUE_BONUS
            # A crew member given up for a blue option before a fight comes back when the
            # fight is won (e.g. the Engi who negotiates with an infected Engi ship).
            if outcome is not None and outcome.stuff.crew < 0 and _fight_ahead(outcome):
                score += CREW * -outcome.stuff.crew * FIGHT_WIN_ODDS
                notes.append("crew member returns after the fight")
        if virus and score != UNAFFORDABLE:
            score += _engi_virus_bonus(choice, outcome, own_crew, notes)
        selectable = score != UNAFFORDABLE and not (aligned and event.shown_types[choice.index] == 1)
        text = event.shown_texts[choice.index] if aligned else choice.text
        scored.append(ScoredChoice(
            index=choice.index,
            text=text.replace("\n", " "),
            score=score,
            summary=", ".join(notes) if notes else "nothing",
            selectable=selectable,
        ))
    return scored


def _engi_virus_bonus(choice: Choice, outcome: Outcome | None, own_crew: int, notes: list[str]) -> float:
    """The Engi virus event, by the order the player asked for: Lanius, then the highest
    Hacking option, then Engi negotiation (only with other crew aboard), then fighting;
    never the purge, which halves our engines and shields for the fight."""
    req = choice.requirement.lower()
    if "lanius" in req or "anaerobic" in req:     # Lanius are "anaerobic" in FTL's data
        notes.append("virus: Lanius option first")
        return 300.0
    if "hacking" in req:
        notes.append(f"virus: hacking level {choice.requirement_level or '?'}")
        return 150.0 + 20.0 * max(1, choice.requirement_level)
    if "engi" in req:
        if own_crew <= 1:
            notes.append("virus: never negotiate with our only crew member")
            return float("-inf")
        notes.append("virus: Engi negotiates (comes back after the fight)")
        return 60.0
    if outcome is not None and any(target == 0 for _, _, _, target in outcome.status_effects):
        notes.append("virus: purging halves our engines and shields")
        return -150.0
    return 0.0


def best_choice(scored: list[ScoredChoice]) -> ScoredChoice | None:
    options = [choice for choice in scored if choice.selectable]
    return max(options, key=lambda choice: choice.score) if options else None
