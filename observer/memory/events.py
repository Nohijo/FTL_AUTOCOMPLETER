"""Read the event on screen and what each of its choices leads to.

FTL builds an event's whole outcome tree when the event is created, so the
outcome behind every choice can be read before choosing:
WorldManager.lastLocationEvent->choices[N].event is what on-screen choice N
leads to (see layout.LocationEvent).
"""

import struct
from dataclasses import dataclass, field

from observer.memory import layout as L
from observer.memory.process import FTLProcess, MemoryReadError

MAX_DEPTH = 4


@dataclass
class Resources:
    scrap: int = 0
    fuel: int = 0
    missiles: int = 0
    drones: int = 0
    crew: int = 0
    crew_type: str = ""
    weapon: str = ""
    weapon_cost: int = 0
    drone: str = ""
    drone_cost: int = 0
    augment: str = ""
    augment_cost: int = 0
    hull_damage: int = 0
    steal: bool = False
    intruders: bool = False
    remove_item: str = ""
    system_id: int = -1               # a system the event installs
    upgrade_id: int = -1              # a system upgraded by upgrade_amount (reactor: 17? see layout)
    upgrade_amount: int = 0
    cloneable: bool = False


@dataclass
class Outcome:
    """One LocationEvent: what happens when it is reached, plus its own choices."""
    address: int
    name: str
    text: str
    stuff: Resources
    reward: Resources
    hostile_ship: bool
    ship_present: bool
    store: bool
    repair: bool
    system_damage: list[tuple[int, int]]       # (system id, amount)
    boarders: tuple[str, int, int] | None      # (race, min, max)
    environment: int = 0                       # 0 none; non-zero: sun, asteroids, storm, ...
    status_effects: list[tuple[int, int, int, int]] = field(default_factory=list)  # (type, system, amount, target)
    modify_pursuit: int = 0
    reveal_map: bool = False
    quest: str = ""
    unlock_ship: int = -1
    ship_name: str = ""                        # ShipEvent.name: the ship definition in events_db.json
    choices: list["Choice"] = field(default_factory=list)


@dataclass
class Choice:
    index: int
    text: str
    requirement: str                           # equipment or crew the choice needs; "" if none
    blue: bool                                 # a requirement-unlocked (blue) option
    hidden_reward: bool
    outcome: Outcome | None
    requirement_level: int = 0                 # level of the required system/equipment


@dataclass
class EventOnScreen:
    text: str
    choices: list[Choice]
    shown_types: list[int]                     # ChoiceText.type of each on-screen choice
    shown_texts: list[str]
    shown_rects: list[tuple[int, int, int, int]]   # (x, y, w, h) of each choice, game coords

    def signature(self) -> tuple:
        return (self.text, tuple(self.shown_texts))


def _blueprint(proc: FTLProcess, address: int) -> tuple[str, int]:
    if address == 0:
        return "", 0
    return proc.string(address + L.Blueprint.NAME), proc.i32(address + L.BLUEPRINT_COST)


def read_resources(proc: FTLProcess, address: int) -> Resources:
    view = proc.view(address, L.ResourceEvent.SIZE)
    R = L.ResourceEvent
    weapon, weapon_cost = _blueprint(proc, view.ptr(R.WEAPON))
    drone, drone_cost = _blueprint(proc, view.ptr(R.DRONE))
    augment, augment_cost = _blueprint(proc, view.ptr(R.AUGMENT))
    return Resources(
        scrap=view.i32(R.SCRAP), fuel=view.i32(R.FUEL), missiles=view.i32(R.MISSILES),
        drones=view.i32(R.DRONES), crew=view.i32(R.CREW),
        crew_type=proc.string_at(view.ptr(R.CREW_TYPE)),
        weapon=weapon, weapon_cost=weapon_cost, drone=drone, drone_cost=drone_cost,
        augment=augment, augment_cost=augment_cost,
        hull_damage=view.i32(R.HULL_DAMAGE), steal=view.bool(R.STEAL),
        intruders=view.bool(R.INTRUDERS), remove_item=proc.string_at(view.ptr(R.REMOVE_ITEM)),
        system_id=view.i32(R.SYSTEM_ID), upgrade_id=view.i32(R.UPGRADE_ID),
        upgrade_amount=view.i32(R.UPGRADE_AMOUNT), cloneable=view.bool(R.CLONEABLE),
    )


def _read_choices(proc: FTLProcess, event: int, depth: int, ancestors: frozenset[int]) -> list[Choice]:
    begin, count = proc.vector_range(event + L.LocationEvent.CHOICES, L.EventChoice.SIZE)
    choices = []
    for index in range(count):
        view = proc.view(begin + index * L.EventChoice.SIZE, L.EventChoice.SIZE)
        target = view.ptr(L.EventChoice.EVENT)
        outcome = read_outcome(proc, target, depth + 1, ancestors) if target and depth < MAX_DEPTH else None
        requirement = proc.string_at(view.ptr(L.EventChoice.REQUIREMENT))
        choices.append(Choice(
            index=index,
            text=proc.string_at(view.ptr(L.EventChoice.TEXT)),
            requirement=requirement,
            blue=bool(requirement) and view.bool(L.EventChoice.BLUE),
            hidden_reward=view.bool(L.EventChoice.HIDDEN_REWARD),
            outcome=outcome,
            requirement_level=view.i32(L.EventChoice.REQUIREMENT_LEVEL) if requirement else 0,
        ))
    return choices


def read_outcome(proc: FTLProcess, event: int, depth: int = 0,
                 ancestors: frozenset[int] = frozenset()) -> Outcome | None:
    # Events can loop back to an ancestor ("try again"); stop there. Siblings may share outcomes.
    if event == 0 or event in ancestors:
        return None
    ancestors = ancestors | {event}
    E = L.LocationEvent
    view = proc.view(event, E.SIZE)

    damage = []
    begin, count = proc.vector_range(event + E.DAMAGE, 0xC)
    if count:
        raw = proc.read(begin, count * 0xC)
        damage = [struct.unpack_from("<ii", raw, i * 0xC) for i in range(count)]

    boarders = None
    amount_max = view.i32(E.BOARDERS + 0xC)
    if amount_max > 0:
        boarders = (proc.string_at(view.ptr(E.BOARDERS)), view.i32(E.BOARDERS + 0x8), amount_max)

    outcome = Outcome(
        address=event,
        name=proc.string_at(view.ptr(E.EVENT_NAME)),
        text=proc.string_at(view.ptr(E.TEXT)),
        stuff=read_resources(proc, event + E.STUFF),
        reward=read_resources(proc, event + E.REWARD),
        hostile_ship=view.bool(E.SHIP_PRESENT) and view.bool(E.SHIP_HOSTILE),
        ship_present=view.bool(E.SHIP_PRESENT),
        store=view.bool(E.STORE),
        repair=view.bool(E.REPAIR),
        system_damage=damage,
        boarders=boarders,
        environment=view.i32(E.ENVIRONMENT),
        modify_pursuit=view.i32(E.MODIFY_PURSUIT),
        reveal_map=view.bool(E.REVEAL_MAP),
        quest=proc.string_at(view.ptr(E.QUEST)),
        unlock_ship=view.i32(E.UNLOCK_SHIP),
        ship_name=proc.string_at(view.ptr(E.SHIP + 0x8)) if view.bool(E.SHIP_PRESENT) else "",
    )
    begin, count = proc.vector_range(event + E.STATUS_EFFECTS, 0x10)
    if 0 < count <= 16:
        raw = proc.read(begin, count * 0x10)
        outcome.status_effects = [struct.unpack_from("<iiii", raw, i * 0x10) for i in range(count)]
    outcome.choices = _read_choices(proc, event, depth, ancestors)
    return outcome


def read_event_on_screen(proc: FTLProcess) -> EventOnScreen | None:
    """The open event dialog with the outcome tree behind each choice, or None."""
    app = proc.ptr(L.P_THE_APP)
    world = proc.ptr(app + L.CApp.WORLD)
    gui = proc.ptr(app + L.CApp.GUI)
    box = gui + L.CommandGui.CHOICE_BOX
    if not proc.bool(box + L.FocusWindow.OPEN):
        return None

    shown_types, shown_texts = [], []
    begin, count = proc.vector_range(box + L.ChoiceBox.CHOICES, L.ChoiceText.SIZE)
    for index in range(count):
        item = proc.view(begin + index * L.ChoiceText.SIZE, 0x10)
        shown_types.append(item.i32(L.ChoiceText.TYPE))
        shown_texts.append(proc.string_at(item.ptr(L.ChoiceText.TEXT)))

    shown_rects = []
    begin, count = proc.vector_range(box + L.ChoiceBox.CHOICE_RECTS, 16)
    if count:
        raw = proc.read(begin, count * 16)
        shown_rects = [struct.unpack_from("<iiii", raw, i * 16) for i in range(count)]

    event = proc.ptr(world + L.WorldManager.LAST_LOCATION_EVENT)
    if event == 0:
        return None
    return EventOnScreen(
        text=proc.string(box + L.ChoiceBox.MAIN_TEXT),
        choices=_read_choices(proc, event, 0, frozenset({event})),
        shown_types=shown_types,
        shown_texts=shown_texts,
        shown_rects=shown_rects,
    )
