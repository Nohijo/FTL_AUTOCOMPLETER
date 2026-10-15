"""Real-time reactions to live game state: aim weapons, manage crew, flag emergencies.

decide() is called several times per second with a fresh GameSnapshot and
returns Intents; bot.py turns them into input. Target priority comes from
brain/combat_policy.decide_combat, the same policy the save-file planner uses.
"""

import math
from dataclasses import dataclass

from brain import arsenal, flagship
from brain.augments import has as has_augment
from brain.combat_policy import decide_combat
from observer.memory.adapter import in_combat, to_legacy_game_state
from observer.memory.layout import SYSTEM_NAMES, TILE_SIZE as TILE
from observer.memory.reader import CrewInfo, DoorInfo, GameSnapshot, RoomInfo

# Enemy systems to aim at, in order, for each target chosen by decide_combat.
TARGET_ORDER = {
    "enemy_weapons": ["weapons", "shields", "pilot", "engines", "oxygen"],
    "enemy_shields": ["shields", "weapons", "pilot", "engines", "oxygen"],
}
# Beams need a click-and-drag across rooms; not supported yet.
UNSUPPORTED_WEAPON_TYPES: set[str] = set()   # every weapon type is aimed now (beams by dragging)

# When nobody is idle, crew are pulled off stations in this order (system type ids).
# The pilot (6) is never pulled during combat: an empty helm means no evasion and no FTL charge.
STATION_SACRIFICE_ORDER = [7, 8, 2, 5, 0, 3, 1]
PILOT = 6

# How urgently a damaged system needs a repair crew (fires are 20+, breaches 18+).
REPAIR_PRIORITY = {
    "oxygen": 10.0, "shields": 9.0, "pilot": 8.0, "engines": 7.5, "weapons": 7.0,
    "medbay": 5.0, "drones": 5.0, "teleporter": 3.0, "cloaking": 4.0, "hacking": 3.0,
    "mind": 3.0, "battery": 4.0, "clonebay": 5.0, "artillery": 4.0,
    "doors": 2.0, "sensors": 1.5,
}

# In combat, only problems at least this urgent justify pulling someone off a station.
PULL_FROM_STATION_IN_COMBAT = 7.0

# Venting: how many rooms away an airlock may be (the rooms in between lose their air too).
VENT_MAX_ROOMS_AWAY = 2
VENT_RETRY_SECONDS = 30.0      # after a door click fails, let crew deal with the room meanwhile
BREATHABLE = 15.0              # room oxygen (%) below which crew are not sent in to fight fires

# A console a crew member stands at only becomes their station once they have been off
# any bot job for this long (so repairing the doors does not make them the door operator).
STATION_SETTLE_SECONDS = 20.0

# Systems a crew member can man, most valuable first (type ids: helm, engines, shields,
# weapons, doors, sensors). Each crew member keeps one as their station.
MANNED_STATIONS = [6, 1, 0, 3, 8, 7]

# Species that are best at each job (FTL races: Rocks are fire-immune, Mantis fight
# hardest, Engis repair twice as fast). Others are used when none of these are free.
SPECIALISTS = {
    "fire": ["rock", "engi", "crystal"],      # Rock are immune to fire; Engi repair (and extinguish) fast
    "breach": ["engi", "rock"],
    "repair": ["engi"],
    "boarders": ["mantis", "rock", "crystal"],
}

# Crew health, as a fraction of max: go to the medbay below SEND_TO_MEDBAY, leave it once
# healed to HEALED, and only fight fires/breaches above FIT_FOR_DUTY.
SEND_TO_MEDBAY = 0.40
NANO_MEDBAY_TRIP = 0.25
FIRE_RETREAT = 0.6
VENT_MIN_SHIP_AIR = 0.4
VENT_GIVE_UP_SECONDS = 25.0       # a vented room still burning after this long: crew / drone take over
STARVE_MIN_AIR = 0.55             # cut the oxygen supply against fires only above this much ship air...
STARVE_STOP_AIR = 0.45            # ...and restore it below this
STARVE_MAX_SECONDS = 30.0           # no fire venting while the whole ship has less air than this                # crew in a burning room go to the medbay below this           # with Engi Med-bots crew heal anywhere: go to the medbay only below this
SEND_TO_MEDBAY_AT_PEACE = 0.85   # outside combat healing is free, so top everyone up
HEALED = 0.90
FIT_FOR_DUTY = 0.40

REISSUE_TARGET_SECONDS = 0.5
VOLLEY_WINDOW_SECONDS = 1.0
TARGET_HOLD_SECONDS = 8.0          # keep shooting one enemy system at least this long
HACK_LEAD_SECONDS = 1.5          # fire the hack pulse when a gun is this close to charged
AWAY_RETREAT = 0.4                 # bring boarders home below this much health
AWAY_HULL_EXIT = 4                 # ... or when the enemy hull is this low (crew die with the ship)
BOARD_MARGIN = 1.5                 # board only when this much stronger than the enemy crew
CLOAK_LEAD_SECONDS = 0.6         # cloak when an enemy weapon is this close to firing
CLOAK_RETRY_SECONDS = 15.0       # the cloak recharges for a while after use
CLOAK_THREAT = 1.5               # Flagship: cloak only for a pod volley at least this harmful (flagship.threat)
SURGE_CLOAK_SAVE = 25.0          # Flagship: keep the cloak for a surge due within this long
BOSS_AWAY_HULL_EXIT = 7          # Flagship: bring boarders home at this hull (it jumps away at 0)
BATTERY_RETRY_SECONDS = 10.0
DEADLOCK_SECONDS = 45.0          # no enemy hull damage for this long in a fight -> leave
DRONE_HULL_REPAIR = 0.7           # launch a hull repair drone below this much hull
FIGHT_MARGIN = 1.3
FIGHT_HOLD = 0.9                   # keep fighting until our side drops below this ratio
BOARDER_WEAKEN_TO = 0.5            # vent a boarded room until the boarders are down to this much health...
BOARDER_VENT_GIVE_UP = 30.0        # ...or for at most this long (they keep the air up, e.g. a door held open)
BOARDER_AIR_WEAK = 30.0            # room oxygen (%) below which breathing boarders are losing health
BOARD_PLAN_DWELL = 6.0             # keep a room's plan (vent / engage / hold) at least this long                 # fight boarders head-on when this much stronger
SPECIES_FIGHT = {"mantis": 1.5, "rock": 1.0, "crystal": 1.1, "engi": 0.5, "zoltan": 0.9,
                 "slug": 1.0, "human": 1.0, "lanius": 1.0, "anaerobic": 1.0}
CREW_AIR_DANGER = 25.0             # room oxygen (%): crew in a room with less air than this -> close every door
VENT_HOLD_SECONDS = 20.0           # after such an emergency, no venting for this long
HELPER_STATIONS_IN_COMBAT = 3      # extra hands mid-fight: from the first stations of STATION_SACRIFICE_ORDER only     # guns aimed together fire within this long of each other
REISSUE_POWER_SECONDS = 1.0
POWERED_SYSTEMS = ("shields", "engines", "oxygen", "weapons", "drones", "medbay",   # managed reactor users
                   "cloaking", "hacking", "mind", "teleporter")
AIR_TOP_UP = 95.0                # room oxygen (%) below this makes air the first system to power
REISSUE_CREW_SECONDS = 2.5
REISSUE_ALERT_SECONDS = 30.0


@dataclass
class Intent:
    kind: str                         # "power_weapon", "power_system", "depower_system", "target", "crew_to_room", ...
    reason: str
    weapon_index: int | None = None
    room: RoomInfo | None = None
    crew: CrewInfo | None = None
    system: str | None = None         # hotkey name for "power_system", e.g. "medbay"
    door: DoorInfo | None = None      # for "door"
    want_open: bool = False
    on_enemy: bool = True             # "system_target": click the room on the enemy ship (else ours)


def ui_blocks_actions(snapshot: GameSnapshot) -> bool:
    return (snapshot.event_open or snapshot.store_open or snapshot.menu_open
            or snapshot.map_open or snapshot.ship_screen_open or snapshot.game_over)


def fire_immune(crew: CrewInfo) -> bool:
    return crew.species.lower() == "rock"


def breathes_nothing(crew: CrewInfo) -> bool:
    return crew.species.lower() == "anaerobic"      # Lanius


def enemy_has_defense_drone(snapshot: GameSnapshot) -> bool:
    enemy = snapshot.enemy
    return enemy is not None and any(d.kind.upper() == "DEFENSE" and d.active for d in enemy.drones)


def _walking(crew: CrewInfo) -> bool:
    """Still on the way somewhere: an order queued or the goal not reached yet."""
    return crew.new_path or abs(crew.goal_x - crew.x) > 2 or abs(crew.goal_y - crew.y) > 2


def room_capacity(snapshot: GameSnapshot, room: RoomInfo) -> int:
    """How many crew can stand in a room: one per tile, minus the medbay's equipment tile
    (FTL marks it filled for both crew and intruders in Room.slots)."""
    tiles = max(1, (room.w // TILE) * (room.h // TILE))
    medbay = snapshot.player.systems.get("medbay")
    return tiles - 1 if medbay is not None and medbay.room_id == room.room_id and tiles > 1 else tiles


def _room(rooms: list[RoomInfo], room_id: int) -> RoomInfo | None:
    return next((room for room in rooms if room.room_id == room_id), None)


def _distance(crew: CrewInfo, room: RoomInfo) -> float:
    return math.hypot(crew.x - (room.x + room.w / 2), crew.y - (room.y + room.h / 2))


def enemy_escaping(snapshot: GameSnapshot) -> bool:
    """The enemy AI decided to flee, or its FTL drive has started charging."""
    enemy = snapshot.enemy
    return enemy is not None and (enemy.escaping or (enemy.jump_goal > 0 and enemy.jump_charge > 0.5))


def heading_to(crew: CrewInfo, room: RoomInfo) -> bool:
    """True when the crew member is in the room, assigned to it, or walking to it."""
    return (crew.room_id == room.room_id
            or crew.slot_room == room.room_id
            or (room.x <= crew.goal_x < room.x + room.w and room.y <= crew.goal_y < room.y + room.h))


class LiveCombatBrain:
    def __init__(self, volley: bool = True):
        self.volley = volley
        self.last_issued: dict[tuple, float] = {}
        # crew address -> (room id, time ordered). A crew member keeps their job until the
        # room is clear, so they are not bounced between rooms.
        self.assignments: dict[int, tuple[int, float]] = {}
        # weapon slot -> enemy system the bot last aimed it at
        self.weapon_targets: dict[int, str] = {}
        # crew address -> system type id of their station (the system they man)
        self.stations: dict[int, int] = {}
        # crew addresses currently sent to the medbay
        self.healing: set[int] = set()
        # doors the bot opened to vent a room, and the rooms currently being vented
        self.opened_doors: set[int] = set()
        self.vented_rooms: set[int] = set()
        # crew address -> when the bot last had them on a job (repair, fire, boarders, medbay)
        self.last_job: dict[int, float] = {}
        # door address -> when clicking it last failed
        self.failed_doors: dict[int, float] = {}
        # while boarders are aboard: crew address -> room they hold (helm, doors, medbay)
        self.boarding_posts: dict[int, int] = {}
        # boarded room -> "engage" / "vent" / "hold" (_assess_boarders), and when venting it began
        self.boarder_plans: dict[int, str] = {}
        self.vent_started_boarders: dict[int, float] = {}
        self.now = 0.0

    def door_failed(self, door_address: int):
        """The game did not accept a door click; stop relying on venting through it for a while."""
        self.failed_doors[door_address] = self.now
        self.opened_doors.discard(door_address)

    def forget(self, crew_address: int):
        """Drop an assignment, e.g. when the game did not accept the order."""
        self.assignments.pop(crew_address, None)

    def _due(self, key: tuple, now: float, interval: float) -> bool:
        if now - self.last_issued.get(key, -math.inf) < interval:
            return False
        self.last_issued[key] = now
        return True

    def _track_game_rate(self, snapshot: GameSnapshot, now: float):
        """How many game seconds pass per decision (weapon charge is counted in game seconds).
        At high game speeds a weapon can go from 'about to fire' to fired between two
        decisions, so timing windows (cloak, hack pulse) are widened to cover one step."""
        player = snapshot.player
        charges = {i: w.charge for i, w in enumerate(player.weapons) if w.powered and 0 < w.charge < w.charge_goal}
        previous, then = getattr(self, "charge_seen", ({}, now))
        gains = [charges[i] - previous[i] for i in charges if i in previous and charges[i] > previous[i]]
        if gains and now > then:
            rate = sorted(gains)[len(gains) // 2] / (now - then)
            self.game_rate = 0.7 * getattr(self, "game_rate", rate) + 0.3 * rate
        self.game_step = getattr(self, "game_rate", 1.0) * max(0.0, now - then)
        self.charge_seen = (charges, now)

    def _lead(self, base: float, cap: float = 3.0) -> float:
        """A timing window in game seconds, at least two decisions long."""
        return min(cap, max(base, 2.0 * getattr(self, "game_step", 0.0)))

    def decide(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        if snapshot.screen != "in_run" or snapshot.player is None or ui_blocks_actions(snapshot):
            return []
        self.now = now
        self._track_game_rate(snapshot, now)
        for address in list(self.assignments) + list(self.healing):
            self.last_job[address] = now
        intents = self._fire_plan(snapshot, now)
        intents += self._manage_power(snapshot, now)
        intents += self._deploy_drones(snapshot, now)
        if in_combat(snapshot):
            intents += self._power_weapons(snapshot, now)
            intents += self._attack(snapshot, now)
            intents += self._cloak(snapshot, now)
            intents += self._hacking(snapshot, now)
            intents += self._teleporter(snapshot, now)
            intents += self._battery(snapshot, now)
            intents += self._flagship_news(snapshot, now)
        intents += self._mind_control(snapshot, now)
        # Boarders aboard: hold the helm, the medbay and the doors room, vent the rest.
        safe = None
        intents += self._assess_boarders(snapshot)
        if self.boarding_mode == "defend":
            safe = self._safe_rooms(snapshot)
            intents += self._lockdown(snapshot, now)
        if safe is None:
            self.boarding_posts.clear()
        # Doors first: venting decides which rooms crew must stay out of.
        intents += self._vent(snapshot, now, safe)
        # One crew order per decision, most urgent first.
        self._update_stations(snapshot)

        def ordered(orders: list[Intent]) -> bool:
            return any(intent.kind == "crew_to_room" for intent in orders)

        crew_orders = self._medical(snapshot, now)
        if not ordered(crew_orders):
            crew_orders += self._man_the_helm(snapshot, now)
        if not ordered(crew_orders) and safe is not None:
            crew_orders += self._boarding_positions(snapshot, now, safe)
        if not ordered(crew_orders):
            crew_orders += self._damage_control(snapshot, now, safe)
        if not ordered(crew_orders) and safe is None:
            crew_orders += self._return_to_stations(snapshot, now)
        intents += crew_orders
        intents += self._emergency(snapshot, now)
        return intents

    # --- fires -------------------------------------------------------------------

    def _fire_plan(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Decide whether to cut the oxygen supply so fires starve: only with fires in several
        rooms, plenty of air ship-wide, nobody in a burning room and no badly hurt crew; for at
        most STARVE_MAX_SECONDS, ended as soon as the fires are out or the air gets low."""
        player = snapshot.player
        burning = [r for r in player.rooms if r.fires]
        air = player.oxygen_total if player.oxygen_total is not None else 1.0
        crew = self._own_crew(snapshot)
        safe = (len(burning) >= 2 and air > STARVE_MIN_AIR and not self._intruders(snapshot)
                and not any(c.room_id in {r.room_id for r in burning} for c in crew)
                and all(c.health >= 0.5 * c.max_health for c in crew))
        was = getattr(self, "starve_fires", False)
        started = getattr(self, "starve_since", now)
        if was and (not burning or air < STARVE_STOP_AIR or now - started > STARVE_MAX_SECONDS):
            self.starve_fires = False
            return [Intent(kind="alert", reason="oxygen back on (fires out, or the air is getting thin)")]
        if not was and safe:
            self.starve_fires, self.starve_since = True, now
            return [Intent(kind="alert", reason=f"fires in {len(burning)} rooms: oxygen off for a while to starve them "
                                                f"(ship air {air:.0%}, nobody in the burning rooms)")]
        return []

    # --- reactor power -------------------------------------------------------

    @staticmethod
    def _spare_power(player) -> int:
        """Bars free to give out: the reactor's, plus the Backup Battery's while it is on."""
        if player.reactor_total is None or player.reactor_used is None:
            return 0
        return player.reactor_total - player.reactor_used + max(0, player.battery_total - player.battery_used)

    def _power_plan(self, snapshot: GameSnapshot) -> dict[str, int]:
        """How many reactor bars each system should get, filling a priority list until the
        reactor runs out. Shields only take whole layers (2 bars). Weapons and drones get what
        it takes to run everything mounted / launched."""
        player = snapshot.player
        systems = player.systems
        combat = in_combat(snapshot)
        crew = self._own_crew(snapshot)
        wounded = any(c.health < c.max_health * 0.999 for c in crew)
        airless = any(r.oxygen is not None and r.oxygen < AIR_TOP_UP for r in player.rooms)

        def usable(name: str) -> int:
            system = systems.get(name)
            return 0 if system is None else min(system.level, system.health)

        weapons_need = sum(w.required_power for w in player.weapons[:4] if w.weapon_type not in UNSUPPORTED_WEAPON_TYPES
                           and not (w.stats and arsenal.kind(w.stats) == "heal"))
        # Drones draw 1-3 bars each (not read): in combat keep what the launched drones use now;
        # _deploy_drones launches more with spare bars.
        drones_now = systems["drones"].power if "drones" in systems else 0
        if combat and "drones" in systems:
            drones_now = max(drones_now, sum(d.power for d in player.drones[:3] if not d.dead
                                             and self._drone_priority(snapshot, d.kind) >= 3))
        caps = {"weapons": min(usable("weapons"), weapons_need)}
        budget = player.reactor_total + player.battery_total     # battery bars while it is on
        plan = {name: 0 for name in POWERED_SYSTEMS if name in systems}

        def give(name: str, upto: int):
            nonlocal budget
            if name not in plan:
                return
            cap = caps.get(name, usable(name))
            add = min(upto - plan[name], cap - plan[name], budget)
            if name == "shields":
                add -= add % 2
            if add > 0:
                plan[name] += add
                budget -= add

        critical = any(c.health < SEND_TO_MEDBAY * c.max_health for c in crew)
        if getattr(self, "starve_fires", False):
            plan_oxygen_cap = 0                # fires starve faster without fresh air (_fire_plan)
        else:
            plan_oxygen_cap = None
        if combat and plan_oxygen_cap is None:
            give("oxygen", 1)                  # crew die without air in a long fight
        if combat:
            if critical:
                give("medbay", 1)              # badly hurt crew: keep the medbay running (_medical sends them)
            if getattr(self, "escaping", False):
                give("engines", 99)            # running: FTL needs engines (and dodging helps)
            give("shields", 2)
            if self._intruders(snapshot):
                give("mind", 99)               # turn a boarder (or free our own crew)
            give("weapons", 99)
            give("engines", 1)                 # dodge, and the ability to jump away
            give("cloaking", 99)               # dodge a whole volley (longer with more bars)
            give("hacking", 99)                # takes their shields / weapons down for the volley
            give("drones", drones_now)
            give("engines", 2)
            if wounded:
                give("medbay", 1)
            give("shields", 99)
            give("engines", 99)
            give("teleporter", 99)
            give("mind", 99)
            if wounded:
                give("medbay", 99)
        else:
            if self._intruders(snapshot):
                give("mind", 99)               # boarders from an event, with no ship around
            if plan_oxygen_cap is None:
                give("oxygen", 1 if not airless else 99)
            give("engines", 1)                 # FTL cannot jump without engine power
            if wounded:
                give("medbay", 99)
            give("shields", 2)
            give("weapons", 99)
            give("engines", 99)
            give("shields", 99)
            if plan_oxygen_cap is None:
                give("oxygen", 99)
            give("drones", 99)
        return plan

    def _manage_power(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Split the reactor by priority (_power_plan) and move bars toward that split: first
        take bars from systems above their share, then add them where they are missing. FTL
        does not give a repaired system its power back, so this is also what brings air,
        shields and engines back after damage. Weapons and drones are powered by their own
        routines (weapon / drone hotkeys); here they only give bars back when over budget."""
        player = snapshot.player
        self.power_claimed = 0
        if player.reactor_total is None or player.reactor_used is None:
            return []
        plan = self._power_plan(snapshot)
        self.power_plan = plan
        spare = self._spare_power(player)
        intents = []
        for name, target in plan.items():
            system = player.systems[name]
            if system.ion_lock or system.power <= target:
                continue
            if self._due(("depower", name), now, REISSUE_POWER_SECONDS):
                intents.append(Intent(kind="depower_system", system=name,
                                      reason=f"taking a bar from {name} ({system.power} -> share {target})"))
                spare += 1
        for name, target in plan.items():
            system = player.systems[name]
            if name in ("weapons", "drones") or system.ion_lock or system.power >= target:
                continue
            step = 2 if name == "shields" else 1      # a shield key press adds a whole layer
            if spare < step or not self._due(("power", name), now, REISSUE_POWER_SECONDS):
                continue
            intents.append(Intent(kind="power_system", system=name,
                                  reason=f"powering {name} ({system.power} -> share {target})"))
            spare -= step
            self.power_claimed += step
        return intents

    # --- hacking, teleporter, mind control ------------------------------------------

    def _enemy_room(self, snapshot: GameSnapshot, names: tuple[str, ...]):
        enemy = snapshot.enemy
        for name in names:
            system = enemy.systems.get(name)
            if system is not None:
                room = _room(enemy.rooms, system.room_id)
                if room is not None:
                    return name, room
        return None

    def _hacking(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Send the hacking probe to the enemy shields once per fight (a drone part), then
        fire the hack pulse just before our guns are ready, so the volley lands on dropped
        shields. A defense drone gets hacked instead when it would shoot our missiles down."""
        player, enemy = snapshot.player, snapshot.enemy
        hack = player.systems.get("hacking")
        if hack is None or hack.power <= 0 or hack.ion_lock or enemy is None:
            return []
        state = hack.special
        if not state.get("targeted"):
            if player.drone_parts < 1 or not self._due(("hack", "launch"), now, 6.0):
                return []
            if enemy.super_shield > 0:
                return []                # a Zoltan shield destroys the probe
            order = ("drones", "shields", "weapons") if enemy_has_defense_drone(snapshot) else ("shields", "weapons")
            if self._intruders(snapshot) and "teleporter" in enemy.systems:
                order = ("teleporter",) + order   # a hacked teleporter pulls their boarders back
            target = self._enemy_room(snapshot, order)
            if target is None:
                return []
            name, room = target
            return [Intent(kind="system_target", system="start_hacking", room=room,
                           reason=f"hacking: probe to the enemy {name} (room {room.room_id})")]
        guns_soon = any(w.powered and w.charge_goal - w.charge <= self._lead(HACK_LEAD_SECONDS) for w in player.weapons)
        if (state.get("drone_arrived") and state.get("can_hack") and not state.get("active") and guns_soon
                and self._due(("hack", "pulse"), now, 4.0)):
            return [Intent(kind="system_key", system="start_hacking",
                           reason="hacking pulse: their system goes down for our volley")]
        return []

    def _teleporter(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Board the enemy only when it pays: a party of healthy fighters that clearly
        outmatches the enemy crew, sent into the room with the fewest defenders (weapons or
        shields preferred). Bring them home when hurt, when the enemy ship is about to blow,
        or when its crew is dead."""
        player, enemy = snapshot.player, snapshot.enemy
        tele = player.systems.get("teleporter")
        self.boarding_party = getattr(self, "boarding_party", set())
        if (tele is None or tele.power <= 0 or tele.ion_lock or enemy is None
                or (enemy.super_shield > 0 and not has_augment(player, "ZOLTAN_BYPASS"))):
            self.boarding_party.clear()
            return []
        state = tele.special
        boss = flagship.is_flagship(enemy)
        away = [c for c in enemy.crew if c.intruder and not c.dead and not c.mind_controlled]
        defenders = [c for c in enemy.crew if not c.intruder and not c.dead]
        if away:
            hurt = any(c.health < AWAY_RETREAT * c.max_health for c in away)
            exit_hull = BOSS_AWAY_HULL_EXIT if boss else AWAY_HULL_EXIT
            # Flagship pods are rooms of their own: once the pod they are in is wrecked there is
            # nothing left to do there, so bring them home to heal and go for the next one.
            pod_done = boss and all(not flagship.room_alive(enemy, c.room_id) for c in away)
            if (hurt or enemy.hull <= exit_hull or not defenders or pod_done) and state.get("can_receive") \
                    and self._due(("tele", "return"), now, 3.0):
                why = ("crew hurt" if hurt else "enemy ship about to blow" if enemy.hull <= exit_hull
                       else "their pod is wrecked" if pod_done else "enemy crew dead")
                return [Intent(kind="system_key", system="ret_tele", reason=f"teleporter: bringing the boarders home ({why})")]
            return []
        if boss:
            return self._board_flagship(snapshot, now, tele)
        crew = [c for c in self._own_crew(snapshot) if c.manning != PILOT]
        fighters = sorted((c for c in crew if c.health >= 0.8 * c.max_health
                           and SPECIES_FIGHT.get(c.species.lower(), 1.0) >= 1.0),
                          key=lambda c: -c.health * SPECIES_FIGHT.get(c.species.lower(), 1.0))
        slots = max(1, state.get("slots") or 2)
        party = fighters[:min(slots, max(0, len(self._own_crew(snapshot)) - 2))]
        enough = len(party) >= (1 if not defenders else 2)
        if not party or not enough or (defenders and self._strength(party) < BOARD_MARGIN * self._strength(defenders)):
            self.boarding_party.clear()
            return []
        tele_room = _room(player.rooms, tele.room_id)
        self.boarding_party = {c.address for c in party}
        waiting = [c for c in party if not heading_to(c, tele_room)]
        if waiting:
            c = waiting[0]
            if self._due(("tele", "gather", c.address), now, REISSUE_CREW_SECONDS):
                return [Intent(kind="crew_to_room", crew=c, room=tele_room,
                               reason=f"boarding party: {c.name} ({c.species}) to the teleporter")]
            return []
        if not state.get("can_send") or any(c.room_id != tele.room_id for c in party):
            return []
        counts = {}
        for c in defenders:
            counts[c.room_id] = counts.get(c.room_id, 0) + 1
        wanted = {s.room_id: n for n, s in enemy.systems.items() if n in ("weapons", "shields", "pilot")}
        room = min(enemy.rooms, key=lambda r: (counts.get(r.room_id, 0), r.room_id not in wanted))
        if self._due(("tele", "send"), now, 4.0):
            return [Intent(kind="system_target", system="send_tele", room=room,
                           reason=f"teleporter: boarding with {', '.join(c.name for c in party)} into room "
                                  f"{room.room_id} ({counts.get(room.room_id, 0)} defenders there)")]
        return []

    def _board_flagship(self, snapshot: GameSnapshot, now: float, tele) -> list[Intent]:
        """Send a boarding party into the most dangerous live weapon pod. The pods are rooms
        of two tiles cut off from the Flagship's crew, so a pair of fighters wrecks the gun
        without a fight; _teleporter brings them home when it is done."""
        player, enemy = snapshot.player, snapshot.enemy
        state = tele.special
        cloak = enemy.systems.get("cloaking")
        if cloak is not None and cloak.special.get("on"):
            return []                        # no teleporting onto a cloaked ship
        targets = []
        for pod in flagship.pods(snapshot):
            inside = [c for c in enemy.crew if c.room_id == pod.room.room_id and not c.intruder and not c.dead]
            targets.append((pod, inside))
        if not targets:
            self.boarding_party.clear()
            return []
        pod, inside = min(targets, key=lambda t: (bool(t[1]), -t[0].threat))
        capacity = max(1, (pod.room.w // TILE) * (pod.room.h // TILE))
        crew = [c for c in self._own_crew(snapshot) if c.manning != PILOT]
        fighters = sorted((c for c in crew if c.health >= 0.7 * c.max_health),
                          key=lambda c: -c.health * SPECIES_FIGHT.get(c.species.lower(), 1.0))
        party = fighters[:min(capacity, max(0, len(self._own_crew(snapshot)) - 2))]
        if not party or (inside and self._strength(party) < BOARD_MARGIN * self._strength(inside)):
            self.boarding_party.clear()
            return []
        tele_room = _room(player.rooms, tele.room_id)
        self.boarding_party = {c.address for c in party}
        waiting = [c for c in party if not heading_to(c, tele_room)]
        if waiting:
            c = waiting[0]
            if self._due(("tele", "gather", c.address), now, REISSUE_CREW_SECONDS):
                return [Intent(kind="crew_to_room", crew=c, room=tele_room,
                               reason=f"Flagship boarding party: {c.name} ({c.species}) to the teleporter")]
            return []
        if not state.get("can_send") or any(c.room_id != tele.room_id for c in party):
            return []
        if self._due(("tele", "send"), now, 4.0):
            return [Intent(kind="system_target", system="send_tele", room=pod.room,
                           reason=f"teleporter: {', '.join(c.name for c in party)} into the Flagship's "
                                  f"{pod.label} (room {pod.room.room_id}) to wreck it")]
        return []

    def _turncoats(self, snapshot: GameSnapshot) -> list[CrewInfo]:
        """Our own crew under enemy mind control (they fight us for a while, then come back)."""
        player = snapshot.player
        return [c for c in player.crew if c.ship_id == player.ship_id and not c.dead
                and c.mind_controlled and not c.intruder]

    def _mind_control(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Turn a boarder against its friends, or in a fight an enemy crew member at their
        weapons (it stops them firing and fights its own crew)."""
        player, enemy = snapshot.player, snapshot.enemy
        mind = player.systems.get("mind")
        if mind is None or mind.power <= 0 or mind.ion_lock or not mind.special.get("can_use"):
            return []
        if not self._due(("mind",), now, 5.0):
            return []
        turncoats = self._turncoats(snapshot)
        if turncoats:
            room = _room(player.rooms, turncoats[0].room_id)
            if room is not None:
                return [Intent(kind="system_target", system="mind", room=room, on_enemy=False,
                               reason=f"mind control: breaking the enemy's hold on {turncoats[0].name} (room {room.room_id})")]
        boarders = self._intruders(snapshot)
        if boarders:
            counts = {}
            for c in boarders:
                counts[c.room_id] = counts.get(c.room_id, 0) + 1
            room = _room(player.rooms, max(counts, key=counts.get))
            if room is not None:
                return [Intent(kind="system_target", system="mind", room=room, on_enemy=False,
                               reason=f"mind control: a boarder in room {room.room_id} turns on its friends")]
        if enemy is not None and in_combat(snapshot) and (enemy.super_shield <= 0
                                                          or has_augment(player, "ZOLTAN_BYPASS")):
            defenders = [c for c in enemy.crew if not c.intruder and not c.dead]
            target = self._enemy_room(snapshot, ("weapons", "pilot", "shields"))
            if defenders and target is not None and any(c.room_id == target[1].room_id for c in defenders):
                return [Intent(kind="system_target", system="mind", room=target[1],
                               reason=f"mind control: the crew at the enemy {target[0]}")]
        return []

    # --- cloaking ---------------------------------------------------------------

    def _cloak(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Cloak just before an enemy volley lands (cloaking makes shots miss). The cloak lasts
        a few seconds and then recharges, so it is saved for a weapon about to fire."""
        player = snapshot.player
        cloak = player.systems.get("cloaking")
        enemy = snapshot.enemy
        if cloak is None or cloak.power <= 0 or cloak.ion_lock or enemy is None or cloak.special.get("on"):
            return []
        about_to_fire = [(label, w) for label, w in flagship.enemy_guns(enemy)
                         if w.powered and w.charge_goal > 0 and w.charge_goal - w.charge <= self._lead(CLOAK_LEAD_SECONDS)]
        if flagship.is_flagship(enemy):
            # The Flagship: the cloak is saved for its power surges (stage 2 drone swarm, stage 3
            # weapon barrage) and otherwise spent only on a pod volley that would really hurt.
            kind = flagship.surge_kind(enemy)
            surge_soon = kind in ("drone swarm", "weapon barrage") and enemy.surge_in is not None
            if surge_soon and enemy.surge_in <= self._lead(CLOAK_LEAD_SECONDS) and self._due(("cloak",), now, CLOAK_RETRY_SECONDS):
                return [Intent(kind="cloak", reason=f"cloaking: the Flagship's {kind} is coming")]
            saving = surge_soon and enemy.surge_in < SURGE_CLOAK_SAVE
            ion_alive = any(flagship.role_of(a.weapon) == "ion" for a in enemy.artillery if a.health > 0)
            harmful = [(label, w) for label, w in about_to_fire
                       if flagship.threat(flagship.role_of(w), player, ion_alive) >= CLOAK_THREAT]
            if harmful and not saving and self._due(("cloak",), now, CLOAK_RETRY_SECONDS):
                return [Intent(kind="cloak", reason=f"cloaking: the Flagship's {harmful[0][0]} is about to fire")]
            return []
        if about_to_fire and self._due(("cloak",), now, CLOAK_RETRY_SECONDS):
            return [Intent(kind="cloak", reason=f"cloaking: enemy {about_to_fire[0][0]} about to fire")]
        # A cloaked ship cannot be boarded: cloak when their teleporter is about to send.
        tele = enemy.systems.get("teleporter")
        if (tele is not None and tele.special.get("charge", 0) >= 0.95 and not self._intruders(snapshot)
                and self._due(("cloak",), now, CLOAK_RETRY_SECONDS)):
            return [Intent(kind="cloak", reason="cloaking: their teleporter is charged (a cloaked ship cannot be boarded)")]
        return []

    # --- Backup Battery, Flagship news -------------------------------------------

    def _battery(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """The Backup Battery lends extra bars for a while, then recharges. Switch it on when
        the reactor is spent and shields, weapons or engines still have room for more power,
        and whenever it is ready against the Flagship."""
        player = snapshot.player
        battery = player.systems.get("battery")
        if battery is None or battery.power <= 0 or battery.ion_lock or battery.special.get("on"):
            return []

        def missing(name: str) -> int:
            system = player.systems.get(name)
            return 0 if system is None or system.ion_lock else max(0, min(system.level, system.health) - system.power)

        unpowered = any(not w.powered and w.missiles_per_shot <= player.missiles for w in player.weapons[:4])
        short = self._spare_power(player) <= 0 and (missing("shields") >= 2 or (missing("weapons") and unpowered)
                                                    or missing("engines"))
        boss = flagship.is_flagship(snapshot.enemy)
        if (short or boss) and self._due(("battery",), now, BATTERY_RETRY_SECONDS):
            why = "the Flagship" if boss else "the reactor is spent and systems want more power"
            return [Intent(kind="system_key", system="activate_battery", reason=f"backup battery on: {why}")]
        return []

    def _flagship_news(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Log the Flagship's state when a stage starts (pods ranked by threat, next surge)."""
        enemy = snapshot.enemy
        stage = enemy.boss_stage if enemy is not None else 0
        if stage == getattr(self, "boss_stage_seen", 0):
            return []
        self.boss_stage_seen = stage
        return [Intent(kind="alert", reason=flagship.describe(snapshot))] if stage else []

    # --- drones ----------------------------------------------------------------

    def _deploy_drones(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Launch the drones the situation calls for (drone hotkeys, default 5/6/7). A launch
        costs one drone part, so drones only go out when they have work: combat and defense
        drones in a fight, the system repair drone when something is broken, the hull repair
        drone when the hull is low, anti-personnel drones when boarders are aboard. The power
        manager takes the power back from the drone system after combat."""
        player = snapshot.player
        system = player.systems.get("drones")
        if system is None or system.ion_lock or not player.drones:
            return []
        combat = in_combat(snapshot)
        boarders = bool(self._intruders(snapshot))
        broken = any(s.health < s.level for s in player.systems.values())
        hull_low = player.hull < DRONE_HULL_REPAIR * player.max_hull

        def wanted(kind: str) -> int:
            return self._drone_priority(snapshot, kind)

        spare_reactor = self._spare_power(player) - getattr(self, "power_claimed", 0)
        spare_system = min(system.level, system.health) - system.power
        candidates = sorted(((wanted(d.kind), i, d) for i, d in enumerate(player.drones[:3])
                             if not d.powered and not d.dead and wanted(d.kind)), key=lambda c: (-c[0], c[1]))
        for priority, index, drone in candidates:
            if not drone.deployed and player.drone_parts < 1:
                continue                     # no parts to launch it
            short = max(drone.power - spare_reactor, drone.power - spare_system)
            if short > 0:
                # Stand down a less useful drone (e.g. a repair drone mid-fight) to free its bars.
                donors = sorted(((wanted(d.kind), i, d) for i, d in enumerate(player.drones[:3])
                                 if d.powered and wanted(d.kind) < priority), key=lambda c: c[0])
                if donors and self._due(("drone_off", donors[0][1]), now, REISSUE_POWER_SECONDS * 2):
                    _, j, donor = donors[0]
                    return [Intent(kind="unpower_drone", weapon_index=j,
                                   reason=f"standing down {donor.blueprint} to power {drone.blueprint}")]
                continue
            if self._due(("drone", index), now, REISSUE_POWER_SECONDS * 2):
                return [Intent(kind="power_drone", weapon_index=index,
                               reason=f"launching {drone.blueprint} ({drone.kind.lower()} drone)")]
        return []

    def _drone_priority(self, snapshot: GameSnapshot, kind: str) -> int:
        """0 = not now, otherwise a priority (higher first)."""
        player = snapshot.player
        combat = in_combat(snapshot)
        boarders = bool(self._intruders(snapshot))
        broken = any(s.health < s.level for s in player.systems.values())
        burning = any(r.fires for r in player.rooms)
        hull_low = player.hull < DRONE_HULL_REPAIR * player.max_hull
        kind = kind.upper()
        if kind == "DEFENSE":
            return 5 if combat else 0
        if kind in ("COMBAT", "SHIELD"):
            return 4 if combat else 0
        if kind == "BATTLE":
            return 4 if boarders else 0
        if kind == "BOARDER":
            return 2 if combat and not enemy_has_defense_drone(snapshot) else 0
        if kind == "REPAIR":
            return 3 if broken or burning else 0     # the system repair drone also fights fires
        if kind == "SHIP_REPAIR":
            return 2 if hull_low and (player.drone_parts >= 3 or has_augment(player, "DRONE_RECOVERY")) else 0
        return 0

    # --- offence -------------------------------------------------------------

    def _power_weapons(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Press the hotkey of each unpowered weapon. WeaponControl::SelectArmament powers an
        unpowered weapon when the weapon system and reactor have room for it."""
        player = snapshot.player
        weapons_system = player.systems.get("weapons")
        if weapons_system is None or weapons_system.ion_lock:
            return []
        spare_system = weapons_system.health - weapons_system.power
        spare_reactor = self._spare_power(player)
        spare_reactor -= getattr(self, "power_claimed", 0)     # bars _manage_power just gave to shields/air

        intents = []
        for index, weapon in enumerate(player.weapons[:4]):
            if weapon.powered or (weapon.stats and arsenal.kind(weapon.stats) == "heal"):
                continue                     # a healing bomb is not fired in fights: no power for it
            if weapon.required_power > min(spare_system, spare_reactor):
                continue
            if weapon.missiles_per_shot > player.missiles:
                continue
            if self._due(("power", index), now, REISSUE_POWER_SECONDS):
                spare_system -= weapon.required_power
                spare_reactor -= weapon.required_power
                intents.append(Intent(kind="power_weapon", weapon_index=index,
                                      reason=f"powering {weapon.blueprint} ({weapon.required_power} power)"))
        return intents

    def _pick_target_room(self, snapshot: GameSnapshot, order: list[str]) -> tuple[str, RoomInfo] | None:
        enemy = snapshot.enemy
        for name in order:
            system = enemy.systems.get(name)
            if system is None or system.health <= 0:
                continue
            room = _room(enemy.rooms, system.room_id)
            if room is not None:
                return name, room
        return None

    def _attack(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        player, enemy = snapshot.player, snapshot.enemy
        boss = flagship.is_flagship(enemy)
        escaping = enemy_escaping(snapshot) and not boss     # the Flagship never runs; it jumps between stages
        # A defense drone shoots missiles down: spend lasers on its control room instead.
        drone_system = enemy.systems.get("drones")
        # Defense Scrambler: their defense drones cannot target anything.
        defended = not has_augment(player, "DEFENSE_SCRAMBLER") and (drone_system is not None and drone_system.health > 0
                    and any(d.kind == "DEFENSE" and d.active for d in enemy.drones))
        order: list[str] = []
        if boss:
            # The Flagship: its weapon pods first, most dangerous to this ship first
            # (brain/flagship.py), then the systems of the stage. Missiles are for this fight.
            launchers_mounted = any(w.missiles_per_shot > 0 for w in player.weapons)
            candidates = flagship.targets(snapshot, defended, launchers_mounted)
            plan_name, use_missiles = f"flagship stage {enemy.boss_stage}", not defended
        elif escaping:
            # A ship cannot charge its FTL drive without a working helm and engines:
            # hit whichever of the two is closest to being knocked out.
            stoppers = sorted(("pilot", "engines"),
                              key=lambda name: enemy.systems[name].health if name in enemy.systems else 99)
            order = stoppers + ["weapons", "shields", "oxygen"]
            plan_name, use_missiles = "stop_escape", not defended
        elif defended:
            order = ["drones", "shields", "weapons", "pilot", "engines", "oxygen"]
            plan_name, use_missiles = "defense_drone_up", False
        else:
            legacy = to_legacy_game_state(snapshot)
            plan = decide_combat(legacy["basic"], legacy["context"])
            order = TARGET_ORDER.get(plan.data.get("target"), TARGET_ORDER["enemy_shields"])
            plan_name, use_missiles = plan.name, plan.data.get("use_missiles", False)
            if has_augment(player, "EXPLOSIVE_REPLICATOR") and player.missiles >= 2:
                use_missiles = True      # half the shots cost nothing

        target = (candidates[0] if candidates else None) if boss else self._pick_target_room(snapshot, order)
        if target is None:
            return []
        system_name, room = target
        # Keep a target for a while: switching every second makes every weapon re-aim (and
        # with autofire, re-target) without adding damage. Switch at once only when fleeing
        # starts or the old target's system is knocked out.
        held = getattr(self, "held_target", None)
        if held and held[0] != system_name and not escaping and now - held[2] < TARGET_HOLD_SECONDS:
            if flagship.room_alive(enemy, held[1].room_id) and any(r.room_id == held[1].room_id for r in enemy.rooms):
                system_name, room = held[0], held[1]
        if not held or held[0] != system_name:
            self.held_target = (system_name, room, now)

        # A cloaked ship dodges most shots, and a charged weapon keeps its charge: hold fire
        # (and autofire) until it uncloaks, then the whole volley lands at once.
        enemy_cloak = enemy.systems.get("cloaking")
        if enemy_cloak is not None and enemy_cloak.special.get("on") and not escaping:
            held_fire = []
            if any(w.auto_firing for w in player.weapons) and self._due(("autofire",), now, 4.0):
                held_fire.append(Intent(kind="autofire", want_open=False,
                                        reason="autofire off: the enemy is cloaked (shots would mostly miss)"))
            if self._due(("cloaked",), now, 15.0):
                held_fire.append(Intent(kind="alert", reason="enemy cloaked: holding fire until it uncloaks"))
            return held_fire

        guns, launchers, beams = [], [], []
        aim: dict[int, tuple[str, RoomInfo]] = {}
        for index, weapon in enumerate(player.weapons[:4]):
            if not weapon.powered or weapon.weapon_type in UNSUPPORTED_WEAPON_TYPES:
                continue
            role = self._weapon_role(snapshot, weapon, escaping)
            if role == "skip":
                continue                     # a healing bomb: never at the enemy
            aim[index] = role or (system_name, room)
            # Leave an aimed weapon alone unless the plan now wants another target (with autofire
            # a weapon keeps its last target, so it has to be re-aimed when the target changes).
            if weapon.targeted and self.weapon_targets.get(index) == aim[index][0]:
                continue
            if weapon.weapon_type == "BEAM":
                beams.append((index, weapon))
            else:
                (launchers if weapon.missiles_per_shot > 0 else guns).append((index, weapon))

        # A weapon aimed while still charging fires by itself the moment it is charged, so
        # weapons are aimed early instead of waiting for the bot to notice the charge.
        chosen = []
        # Missiles and bombs ignore shields: aim them at once. Bombs teleport aboard, so a
        # defense drone cannot shoot them down: they fly even when missiles are held back.
        bombs_ok = use_missiles or defended
        for i, w in launchers:
            is_bomb = bool(w.stats) and arsenal.kind(w.stats) == "bomb"
            if (use_missiles or (is_bomb and bombs_ok)) and player.missiles >= w.missiles_per_shot:
                chosen.append((i, w))
        # A gun with more shots than the enemy has shield layers gets damage through on its
        # own: aim it at once too.
        layers = enemy.max_shield_layers + enemy.super_shield
        solo = [(i, w) for i, w in guns if w.shots > layers]
        group = [(i, w) for i, w in guns if w.shots <= layers]
        chosen += solo
        # The others only hurt as one volley (a shield layer comes back about every 2 s), so
        # aim them together once all of them will be charged within VOLLEY_WINDOW_SECONDS.
        if group and (escaping or not self.volley
                      or all(w.charge_goal - w.charge <= VOLLEY_WINDOW_SECONDS for _, w in group)):
            chosen += group
        # A beam loses one damage per shield layer it crosses and fires the moment it is aimed:
        # aim it when charged and it gets through the shields as they are now (after a volley).
        up_now = enemy.shield_layers + enemy.super_shield
        # (A beam with no hull damage - fire or anti-crew beams - only works on dropped shields.)
        chosen += [(i, w) for i, w in beams if w.ready and (w.damage > up_now or (w.damage <= 0 and up_now == 0))]

        # Autofire keeps each weapon on its target and re-fires it the moment it charges (no
        # aiming delay). Good when every gun gets through alone and missiles may be spent;
        # bad when guns must land together, missiles should be saved, or a beam would hit
        # shields that are up.
        armed = guns + launchers + beams
        autofire = bool(armed) and (escaping or (not group and (not launchers or use_missiles)
                                                 and all(w.damage > layers for _, w in beams)))
        current = any(w.auto_firing for _, w in armed)
        intents = []
        if armed and autofire != current and self._due(("autofire",), now, 4.0):
            why = ("every gun gets through alone" if autofire else
                   "guns must fire together" if group else "saving missiles" if launchers and not use_missiles
                   else "beams would hit raised shields")
            intents.append(Intent(kind="autofire", want_open=autofire,
                                  reason=f"autofire {'on' if autofire else 'off'}: {why}"))

        for index, weapon in chosen:
            label, where = aim.get(index, (system_name, room))
            if self._due(("target", index), now, REISSUE_TARGET_SECONDS):
                self.weapon_targets[index] = label
                intents.append(Intent(
                    kind="target",
                    weapon_index=index,
                    room=where,
                    reason=f"{plan_name}: {weapon.blueprint} -> enemy {label} (room {where.room_id})",
                ))
        return intents

    def _weapon_role(self, snapshot: GameSnapshot, weapon, escaping: bool):
        """Where this kind of weapon does the most, when that is not the plan's target:
        ion at the shields (each ion hit locks a layer down for the guns), fire / anti-crew
        beams at the room with the most enemy crew. Returns (label, room), None for the
        plan's target, or "skip" for a weapon that must not be fired at the enemy."""
        enemy = snapshot.enemy
        kind = arsenal.kind(weapon.stats) if weapon.stats else weapon.weapon_type.lower()
        if kind == "heal":
            return "skip"
        if escaping:
            return None
        if kind == "ion" and enemy.max_shield_layers > 0 and enemy.super_shield <= 0:
            target = self._pick_target_room(snapshot, ["shields"])
            if target is not None:
                return target
        if kind == "beam" and weapon.damage <= 0:
            counts: dict[int, int] = {}
            for c in enemy.crew:
                if not c.intruder and not c.dead:
                    counts[c.room_id] = counts.get(c.room_id, 0) + 1
            if counts:
                room = _room(enemy.rooms, max(counts, key=counts.get))
                if room is not None:
                    return f"crew ({counts[room.room_id]} there)", room
        return None

    # --- defence -------------------------------------------------------------

    def _own_crew(self, snapshot: GameSnapshot) -> list[CrewInfo]:
        """Crew the bot can give orders to: aboard, alive, ours, and not a drone."""
        player = snapshot.player
        return [c for c in player.crew
                if c.ship_id == player.ship_id and not c.dead and c.health > 0 and not c.intruder
                and not c.mind_controlled and c.room_id >= 0 and "drone" not in f"{c.name} {c.species}".lower()]

    def _intruders(self, snapshot: GameSnapshot) -> list[CrewInfo]:
        """Enemy crew standing on the player ship (boarders), from either crew list."""
        player, enemy = snapshot.player, snapshot.enemy
        crew = list(player.crew) + (list(enemy.crew) if enemy else [])
        return [c for c in crew if c.ship_id == player.ship_id and not c.dead
                and (c.intruder or c.mind_controlled)]

    # --- boarders: hold the helm, the medbay and the doors room ----------------

    def _strength(self, crew: list[CrewInfo]) -> float:
        """Fighting strength: health weighted by how hard the species hits."""
        return sum(c.health * SPECIES_FIGHT.get(c.species.lower(), 1.0) for c in crew)

    def _assess_boarders(self, snapshot: GameSnapshot) -> list[Intent]:
        """A plan for every room with boarders in it, reassessed each decision:
          - engage: send crew in (clearly stronger there, or they reached the helm, the
            medbay or the doors room, where our crew are anyway; the medbay heals our side)
          - vent:   open a path to space and let the air out first (only through rooms nobody
            of ours is in), until they are weakened, flee the room, or venting stops working
          - hold:   neither is safe: stay in the medbay / at the helm and let them come
        A plan is kept at least BOARD_PLAN_DWELL seconds unless it becomes impossible, and
        engaging uses hysteresis (FIGHT_MARGIN to start, FIGHT_HOLD to give up): no loops.
        boarding_mode is "fight" when every group is engaged, else "defend" (posts held,
        only the rooms planned for venting are vented). Each change is announced."""
        player = snapshot.player
        now = self.now
        boarders = [c for c in self._intruders(snapshot) if c.intruder and not c.mind_controlled]
        plans: dict[int, str] = getattr(self, "boarder_plans", {})
        since: dict[int, float] = getattr(self, "boarder_plan_since", {})
        alerts: list[Intent] = []
        if not boarders:
            if plans:
                alerts.append(Intent(kind="alert", reason="boarders gone: back to normal duty"))
            self.boarder_plans, self.boarder_plan_since, self.boarding_mode = {}, {}, None
            self.boarder_seen = {}
            return alerts

        groups: dict[int, list[CrewInfo]] = {}
        for c in boarders:
            groups.setdefault(c.room_id, []).append(c)
        safe = self._safe_rooms(snapshot)
        keep_rooms = set(safe.values())
        oxygen = player.systems.get("oxygen")
        own = self._own_crew(snapshot)
        breathers = [c for c in own if not breathes_nothing(c)]
        occupied = {c.room_id for c in breathers} | {c.room_id for c in self._turncoats(snapshot)}
        no_vent = keep_rooms | occupied | ({oxygen.room_id} if oxygen is not None else set())
        doors = player.systems.get("doors")
        doors_work = (bool(player.doors) and doors is not None and doors.health > 0 and doors.power > 0
                      and doors.ion_lock == 0)
        medbay = player.systems.get("medbay")
        medbay_heals = medbay is not None and medbay.health > 0 and medbay.power > 0
        fighters = sorted((c for c in own if c.health >= FIT_FOR_DUTY * c.max_health
                           and not (in_combat(snapshot) and c.manning == PILOT)),
                          key=lambda c: -c.health * SPECIES_FIGHT.get(c.species.lower(), 1.0))
        combat = in_combat(snapshot)

        new_plans: dict[int, str] = {}
        notes: dict[int, str] = {}
        for room_id, group in groups.items():
            room = _room(player.rooms, room_id)
            if room is None:
                continue
            theirs = self._strength(group)
            hp = sum(c.health for c in group) / max(1.0, sum(c.max_health for c in group))
            air = room.oxygen if room.oxygen is not None else 100.0
            breathing = all(not breathes_nothing(c) for c in group)
            if breathing and air < BOARDER_AIR_WEAK:
                theirs *= 0.6                      # they are suffocating
            team = fighters[:room_capacity(snapshot, room)]
            ours = self._strength(team) * (0.7 if air < BOARDER_AIR_WEAK + 15 else 1.0)
            if medbay_heals and room_id == medbay.room_id:
                ours *= 1.5                        # our side heals while fighting there
            system = next((n for n, sy in player.systems.items() if sy.room_id == room_id), None)
            critical = combat and system in ("shields", "weapons", "pilot", "oxygen", "medbay", "engines")
            can_vent = (doors_work and breathing and room_id not in no_vent
                        and bool(self._vent_path(player, room_id, no_vent)[0])
                        and now - self.vent_started_boarders.get(room_id, now) < BOARDER_VENT_GIVE_UP)
            previous = plans.get(room_id)
            margin = FIGHT_HOLD if previous == "engage" else FIGHT_MARGIN
            if room_id in keep_rooms:
                plan, why = "engage", f"they reached our {next(r for r, i in safe.items() if i == room_id)}"
            elif breathing and air < BREATHABLE and any(not breathes_nothing(c) for c in team):
                plan, why = ("vent" if can_vent else "hold"), f"almost no air in there ({air:.0f}%): they suffocate, we would too"
            elif can_vent and hp > BOARDER_WEAKEN_TO and not (critical and ours >= FIGHT_MARGIN * theirs):
                plan, why = "vent", f"venting first to weaken them ({hp:.0%} health, air {air:.0f}%)"
            elif ours >= margin * theirs and team:
                plan, why = "engage", f"we are stronger there ({ours:.0f} vs {theirs:.0f})"
            elif can_vent:
                plan, why = "vent", f"too strong to fight ({ours:.0f} vs {theirs:.0f}): venting"
            else:
                plan, why = "hold", (f"too strong to fight ({ours:.0f} vs {theirs:.0f}) and no safe way to vent: "
                                     f"holding the medbay and the helm")
            forced = (previous == "vent" and not can_vent) or room_id in keep_rooms
            if previous and plan != previous and not forced and now - since.get(room_id, now) < BOARD_PLAN_DWELL:
                plan = previous                    # no flip-flopping
            if plan != previous:
                since[room_id] = now
                species = ", ".join(sorted({c.species for c in group}))
                alerts.append(Intent(kind="alert", reason=f"boarders in room {room_id}{f' ({system})' if system else ''}: "
                                                          f"{len(group)} {species}; {plan}: {why}"))
            if plan == "vent":
                self.vent_started_boarders.setdefault(room_id, now)
            else:
                self.vent_started_boarders.pop(room_id, None)
            new_plans[room_id] = plan
            notes[room_id] = why

        # Track them: a group that changed rooms is announced (at most every few seconds).
        seen = getattr(self, "boarder_seen", {})
        moved = [(seen[c.address], c.room_id) for c in boarders if c.address in seen and seen[c.address] != c.room_id]
        if moved and self._due(("boarders_moved",), now, 5.0):
            alerts.append(Intent(kind="alert", reason="boarders moving: " + ", ".join(
                f"room {a} -> {b}" for a, b in sorted(set(moved)))))
        self.boarder_seen = {c.address: c.room_id for c in boarders}
        for room_id in list(self.vent_started_boarders):
            if room_id not in groups:
                self.vent_started_boarders.pop(room_id)       # they left the room: venting it worked

        self.boarder_plans = new_plans
        self.boarder_plan_since = {r: t for r, t in since.items() if r in new_plans}
        mode = "fight" if new_plans and all(p == "engage" for p in new_plans.values()) else "defend"
        previous_mode, self.boarding_mode = getattr(self, "boarding_mode", None), mode
        if mode != previous_mode:
            ours_all, theirs_all = self._strength(fighters), self._strength(boarders)
            alerts.append(Intent(kind="alert", reason=(
                f"{len(boarders)} boarder(s): " + ("fighting them everywhere" if mode == "fight" else
                                                   "helm, doors and medbay held; vent / fight room by room")
                + f" (our {ours_all:.0f} vs their {theirs_all:.0f})")))
        return alerts

    def _lockdown(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Crystal crew can seal the room they stand in. When defending, a Crystal in a safe
        room seals it as boarders arrive next door, keeping them out."""
        if getattr(self, "boarding_mode", None) != "defend":
            return []
        safe = set(self._safe_rooms(snapshot).values())
        boarders = {c.room_id for c in self._intruders(snapshot)}
        neighbours = {}
        for door in snapshot.player.doors:
            if not door.airlock:
                neighbours.setdefault(door.room1, set()).add(door.room2)
                neighbours.setdefault(door.room2, set()).add(door.room1)
        for c in self._own_crew(snapshot):
            if (c.species.lower() == "crystal" and c.room_id in safe and c.room_id not in boarders
                    and neighbours.get(c.room_id, set()) & boarders and self._due(("lockdown", c.address), now, 50.0)):
                return [Intent(kind="lockdown", crew=c, reason=f"lockdown: {c.name} seals room {c.room_id} against boarders next door")]
        return []

    def _safe_rooms(self, snapshot: GameSnapshot) -> dict[str, int]:
        """Rooms that keep their air while boarders are aboard: role -> room id."""
        rooms = {}
        for role, name in (("helm", "pilot"), ("medbay", "medbay"), ("doors", "doors")):
            system = snapshot.player.systems.get(name)
            if system is not None:
                rooms[role] = system.room_id
        return rooms

    def _boarding_positions(self, snapshot: GameSnapshot, now: float, safe: dict[str, int]) -> list[Intent]:
        """Pilot at the helm, one crew member manning the doors (one level stronger, so boarders
        have to break through), everyone else waiting in the medbay where they heal while
        fighting. One order per decision."""
        player = snapshot.player
        # Crew on their way to be healed stay on that job (no flip-flopping with _medical).
        crew = [c for c in self._own_crew(snapshot) if not c.fighting and c.address not in self.healing]
        if not crew:
            return []
        alive = {c.address for c in crew}
        self.boarding_posts = {a: r for a, r in self.boarding_posts.items() if a in alive}

        def rid(room_id):
            return _room(player.rooms, room_id)

        posts: dict[int, int] = {}
        remaining = list(crew)
        if "helm" in safe:
            pilot = next((c for c in remaining if self.stations.get(c.address) == PILOT), None)
            pilot = pilot or min(remaining, key=lambda c: _distance(c, rid(safe["helm"])))
            posts[pilot.address] = safe["helm"]
            remaining.remove(pilot)
        doors = player.systems.get("doors")
        if "doors" in safe and remaining and doors is not None and doors.health > 0:
            operator = next((c for c in remaining if self.stations.get(c.address) == 8), None)
            operator = operator or min(remaining, key=lambda c: _distance(c, rid(safe["doors"])))
            posts[operator.address] = safe["doors"]
            remaining.remove(operator)
        medbay = player.systems.get("medbay")
        gather = safe.get("medbay") if medbay is not None and medbay.health > 0 else safe.get("helm")
        for c in remaining:
            if gather is not None:
                posts[c.address] = gather
        self.boarding_posts = posts

        for c in crew:
            room = rid(posts.get(c.address, -1))
            if room is None or heading_to(c, room) or c.address in self.assignments:
                continue
            if self._due(("boarding", c.address), now, REISSUE_CREW_SECONDS):
                role = next((r for r, room_id in safe.items() if room_id == room.room_id), "post")
                return [Intent(kind="crew_to_room", crew=c, room=room,
                               reason=f"boarders aboard: {c.name} to the {role} (room {room.room_id})")]
        return []

    def _man_the_helm(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """At a dangerous beacon (sun, pulsar, asteroids...) with the FTL drive ready, an
        empty helm is the emergency: nobody can jump the ship out."""
        player = snapshot.player
        helm = player.systems.get("pilot")
        if (not snapshot.beacon_hazards or helm is None or helm.manned or helm.health <= 0
                or not player.jump_ready or self._intruders(snapshot)):
            return []
        room = _room(player.rooms, helm.room_id)
        crew = [c for c in self._own_crew(snapshot) if not c.fighting]
        if room is None or not crew or any(heading_to(c, room) for c in crew):
            return []
        pilot = min(crew, key=lambda c: _distance(c, room))
        if not self._due(("helm", pilot.address), now, REISSUE_CREW_SECONDS):
            return []
        self.forget(pilot.address)
        self.stations[pilot.address] = PILOT
        return [Intent(kind="crew_to_room", crew=pilot, room=room,
                       reason=f"{', '.join(snapshot.beacon_hazards)} here: {pilot.name} to the helm so we can jump")]

    # --- venting: fires and boarders ------------------------------------------

    def _vent_path(self, player, start: int, occupied: set[int]) -> tuple[set[int], set[int]]:
        """Doors to open so `start` loses its air: its own airlocks, or the interior doors
        along the shortest crew-free path (up to two rooms away) to a room with airlocks.
        Returns (door addresses, rooms that will be vented)."""
        airlocks: dict[int, list] = {}
        interior: dict[int, list] = {}
        for door in player.doors:
            if door.airlock:
                room = door.room1 if door.room1 >= 0 else door.room2
                airlocks.setdefault(room, []).append(door)
            else:
                interior.setdefault(door.room1, []).append(door)
                interior.setdefault(door.room2, []).append(door)

        frontier = [(start, [], [start])]
        seen = {start}
        for _ in range(VENT_MAX_ROOMS_AWAY + 1):
            next_frontier = []
            for room, doors, rooms in frontier:
                if room in airlocks:
                    return {d.address for d in doors + airlocks[room]}, set(rooms)
                for door in interior.get(room, []):
                    other = door.other_side(room)
                    if other in seen or other in occupied:
                        continue
                    seen.add(other)
                    next_frontier.append((other, doors + [door], rooms + [other]))
            frontier = next_frontier
        return set(), set()

    def _vent(self, snapshot: GameSnapshot, now: float, safe: dict[str, int] | None = None) -> list[Intent]:
        """Open doors to space to put out fires in empty rooms, or (with boarders aboard)
        to vent every room except the safe ones; close them again once the job is done.
        One door per decision."""
        player = snapshot.player
        doors_system = player.systems.get("doors")
        oxygen = player.systems.get("oxygen")
        # Same test as ShipManager::DoorsFunction: doors can only be worked while the doors
        # system is undamaged, powered and not ion-locked.
        # Venting only empties rooms nobody is in, so it is worth doing even with the oxygen
        # system down: it stops fires spreading toward the crew.
        can_vent = (bool(player.doors) and doors_system is not None and doors_system.health > 0
                    and doors_system.power > 0 and doors_system.ion_lock == 0)

        own = self._own_crew(snapshot)
        breathers = [c for c in own if not breathes_nothing(c)]   # Lanius need no air
        occupied = {c.room_id for c in breathers} | {c.slot_room for c in breathers if c.slot_room >= 0}
        # Our own crew under enemy mind control are still ours: their rooms keep their air.
        occupied |= {c.room_id for c in self._turncoats(snapshot) if not breathes_nothing(c)}
        # Venting kills boarders who breathe; Lanius do not, so their rooms are not vented.
        boarded = {c.room_id for c in self._intruders(snapshot) if c.intruder and not breathes_nothing(c)}
        air = {r.room_id: r.oxygen for r in player.rooms}

        # Safety first: crew short of air while doors the bot opened stand open -> close
        # every door at once and wait before venting again.
        danger = CREW_AIR_DANGER / 2 if has_augment(player, "O2_MASKS") else CREW_AIR_DANGER   # half damage from thin air
        choking = [c for c in own if (air.get(c.room_id) if air.get(c.room_id) is not None else 1.0) < danger]
        if choking and (self.opened_doors or self.vented_rooms) and can_vent:
            if self._due(("doors_all", False), now, 2.0):
                self.opened_doors.clear()
                self.vented_rooms = set()
                self.vent_hold_until = now + VENT_HOLD_SECONDS
                names = ", ".join(c.name for c in choking)
                return [Intent(kind="doors_all", want_open=False,
                               reason=f"{names} short of air; closing every door")]
            return []
        if now < getattr(self, "vent_hold_until", 0.0):
            can_vent = False
        # A broken oxygen system has to be repaired: keep its room breathable (never vent it
        # or vent through it).
        if oxygen is not None and oxygen.health < oxygen.level:
            occupied.add(oxygen.room_id)

        wanted_open: set[int] = set()
        vented: set[int] = set()
        reasons: dict[int, str] = {}
        if can_vent and safe is not None and any(_walking(c) for c in own):
            # Boarders aboard but crew still on the move: venting now would empty rooms they
            # walk through. Wait until everyone stands in a room that keeps its air.
            self.vented_rooms = self.vented_rooms & set()
            return []
        if can_vent and safe is not None:
            # Defending against boarders: vent only the rooms they are in, along a path to space
            # that never touches a room our crew are in or heading to, the helm, the medbay,
            # the doors room or the oxygen room. Every other door stays shut.
            keep = set(safe.values()) | occupied | ({oxygen.room_id} if oxygen is not None else set())
            planned = {r for r, plan in self.boarder_plans.items() if plan == "vent"}
            for room_id in sorted((boarded & planned) - keep):
                doors, rooms = self._vent_path(player, room_id, keep)
                if not doors or any(self.now - self.failed_doors.get(d, -1e9) < VENT_RETRY_SECONDS for d in doors):
                    continue
                wanted_open |= doors
                vented |= rooms
                for address in doors:
                    reasons.setdefault(address, f"boarders in room {room_id}: venting it to weaken them")
        elif can_vent:
            fighting = {room_id for room_id, _ in self.assignments.values()}
            self.vent_started = getattr(self, "vent_started", {})
            self.vent_given_up = getattr(self, "vent_given_up", {})
            for room in player.rooms:
                if room.room_id in occupied or (room.fires and room.room_id in fighting):
                    continue          # crew are (or will be) in there fighting it: one plan per room
                if room.fires and now < self.vent_given_up.get(room.room_id, 0.0):
                    continue          # venting did not put it out: crew / repair drone handle it
                if room.room_id in boarded:
                    if getattr(self, "boarding_mode", None) == "fight":
                        continue          # our crew are going in to fight them
                    why = f"trap the boarders in room {room.room_id} and vent their air"
                elif room.fires:
                    if (player.oxygen_total or 1.0) < VENT_MIN_SHIP_AIR:
                        continue          # the ship is short of air already: fight it instead
                    why = f"vent room {room.room_id} to put out {room.fires} fire tile(s)"
                else:
                    continue
                doors, rooms = self._vent_path(player, room.room_id, occupied)
                if any(self.now - self.failed_doors.get(d, -1e9) < VENT_RETRY_SECONDS for d in doors):
                    continue  # a door on this path just failed: let crew handle it instead
                if doors and room.fires:
                    started = self.vent_started.setdefault(room.room_id, now)
                    if now - started > VENT_GIVE_UP_SECONDS:
                        self.vent_given_up[room.room_id] = now + 60.0
                        self.vent_started.pop(room.room_id, None)
                        continue      # still burning after venting long enough: try something else
                if doors:
                    wanted_open |= doors
                    vented |= rooms
                    for address in doors:
                        reasons.setdefault(address, why)
        self.vented_rooms = vented
        for room_id in list(getattr(self, "vent_started", {})):
            if not any(r.room_id == room_id and r.fires for r in player.rooms):
                self.vent_started.pop(room_id, None)      # out: venting worked

        by_address = {door.address: door for door in player.doors}
        # Containment: an open door next to a burning room lets the fire spread. Close it
        # unless it is on a vent path or crew are on the move (they open doors as they pass).
        burning = {r.room_id for r in player.rooms if r.fires}
        if can_vent and burning and safe is None and not any(_walking(c) for c in own):
            for door in player.doors:
                if (door.open and not door.airlock and door.address not in wanted_open
                        and {door.room1, door.room2} & burning and self._due(("contain", door.address), now, 6.0)):
                    return [Intent(kind="door", door=door, want_open=False,
                                   reason="closing a door next to a fire to stop it spreading")]
        # Doors are opened one by one, only those on a vent path: Open All Doors would empty
        # the rooms the crew shelter in until each of their doors is closed again.
        for address in wanted_open:
            door = by_address[address]
            if not door.open and self._due(("door", address), now, REISSUE_POWER_SECONDS):
                self.opened_doors.add(address)
                return [Intent(kind="door", door=door, want_open=True, reason=reasons[address])]
        for address in list(self.opened_doors):
            door = by_address.get(address)
            if door is None:
                self.opened_doors.discard(address)
                continue
            if address not in wanted_open:
                if not door.open:
                    self.opened_doors.discard(address)
                    continue
                still_open = [a for a in self.opened_doors if a not in wanted_open
                              and by_address.get(a) is not None and by_address[a].open]
                if not wanted_open and len(still_open) > 1 and self._due(("doors_all", False), now, 3.0):
                    # Done venting everywhere: Close All Doors in one key press.
                    self.opened_doors.clear()
                    return [Intent(kind="doors_all", want_open=False,
                                   reason="done venting; closing every door to let the air back in")]
                if self._due(("door", address), now, REISSUE_POWER_SECONDS):
                    return [Intent(kind="door", door=door, want_open=False,
                                   reason="done venting; closing the door to let the air back in")]
        return []

    def _station_room(self, snapshot: GameSnapshot, system_id: int) -> RoomInfo | None:
        system = next((s for s in snapshot.player.systems.values() if s.type_id == system_id), None)
        return _room(snapshot.player.rooms, system.room_id) if system else None

    def _update_stations(self, snapshot: GameSnapshot):
        """Learn stations from where crew settle by themselves (e.g. moved by the player), and
        keep the most important stations covered."""
        crew = self._own_crew(snapshot)
        alive = {c.address for c in crew}
        self.stations = {a: s for a, s in self.stations.items() if a in alive}
        self.healing &= alive
        for c in crew:
            off_duty = (c.address not in self.assignments and c.address not in self.healing
                        and self.now - self.last_job.get(c.address, -1e9) > STATION_SETTLE_SECONDS)
            # A console reached because of a job (e.g. repairing the doors) is not a new station.
            if c.manning in MANNED_STATIONS and off_duty:
                for other, station in list(self.stations.items()):
                    if station == c.manning and other != c.address:
                        del self.stations[other]
                self.stations[c.address] = c.manning

        # Cover the stations in priority order: if a more important one has nobody, the crew
        # member on the least important station moves up.
        existing = [s for s in MANNED_STATIONS
                    if any(sys.type_id == s and sys.health > 0 for sys in snapshot.player.systems.values())]
        for station in existing:
            if station in self.stations.values():
                continue
            lower = [(existing.index(s), a) for a, s in self.stations.items()
                     if s in existing and existing.index(s) > existing.index(station)]
            if lower:
                _, address = max(lower)
                self.stations[address] = station

    def _medical(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        player = snapshot.player
        crew = self._own_crew(snapshot)
        medbay = player.systems.get("medbay")
        medbay_room = _room(player.rooms, medbay.room_id) if medbay else None
        if medbay is None or medbay_room is None or medbay.health <= 0:
            return []

        intents = []
        oxygen = player.systems.get("oxygen")
        emergency = (any(room.fires or room.breaches for room in player.rooms) or bool(self._intruders(snapshot))
                     or (oxygen is not None and oxygen.health < oxygen.level) or bool(snapshot.beacon_hazards))
        # Top everyone up only when the ship is truly quiet; otherwise only the badly hurt go.
        threshold = SEND_TO_MEDBAY if in_combat(snapshot) or emergency else SEND_TO_MEDBAY_AT_PEACE
        if has_augment(snapshot.player, "NANO_MEDBAY"):
            threshold = min(threshold, NANO_MEDBAY_TRIP)   # they heal anywhere (slower)
        burning_rooms = {r.room_id for r in snapshot.player.rooms if r.fires}
        burning = lambda c: c.room_id in burning_rooms and not fire_immune(c)   # fire hurts fast: leave earlier
        wounded = sorted((c for c in crew if c.health < (max(threshold, FIRE_RETREAT) if burning(c) else threshold) * c.max_health),
                         key=lambda c: c.health / c.max_health)
        if wounded and medbay.power == 0:
            spare = (player.reactor_total - player.reactor_used) if player.reactor_total is not None else 0
            if spare >= 1 and self._due(("power", "medbay"), now, REISSUE_POWER_SECONDS):
                intents.append(Intent(kind="power_system", system="medbay",
                                      reason=f"powering the medbay for {wounded[0].name}"))

        for c in wounded:
            if heading_to(c, medbay_room):
                self.healing.add(c.address)
                continue
            if self._due(("medbay", c.address), now, REISSUE_CREW_SECONDS):
                self.forget(c.address)          # drop any other job
                self.healing.add(c.address)
                intents.append(Intent(kind="crew_to_room", crew=c, room=medbay_room,
                                      reason=f"{c.name} at {c.health:.0f}/{c.max_health:.0f} hp; going to the medbay"))
                return intents

        for c in crew:
            if c.address in self.healing and c.health >= HEALED * c.max_health:
                self.healing.discard(c.address)   # _return_to_stations sends them back
        return intents

    def _hazards(self, snapshot: GameSnapshot) -> list[tuple[float, RoomInfo, str, str, int]]:
        """Rooms that need crew, most urgent first: (priority, room, what, job, crew wanted)."""
        player = snapshot.player
        found: dict[int, list] = {}

        def add(room: RoomInfo, priority: float, what: str, job: str, wanted: int = 1):
            # entry: [best priority, room, notes, job of the best, crew wanted, sum of priorities]
            # Several crew work a room at once (one per tile), so the jobs' crew add up;
            # _damage_control caps the total at the room's capacity.
            entry = found.setdefault(room.room_id, [0.0, room, [], job, 0, 0.0])
            if priority > entry[0]:
                entry[0], entry[3] = priority, job
            entry[2].append(what)
            entry[4] += wanted
            entry[5] += priority

        intruders_by_room: dict[int, int] = {}
        for c in self._intruders(snapshot):
            intruders_by_room[c.room_id] = intruders_by_room.get(c.room_id, 0) + 1
        for room_id, count in intruders_by_room.items():
            room = _room(player.rooms, room_id)
            if self.boarder_plans.get(room_id, "engage") != "engage":
                continue                         # being vented, or held off: nobody goes in
            if room is not None:
                # Outnumber them: one more defender than there are boarders.
                system = next((n for n, sy in player.systems.items() if sy.room_id == room_id), None)
                stake = 0.5 * REPAIR_PRIORITY.get(system, 0.0) if system else 0.0
                where = f" on the {system}" if system else ""
                add(room, 21 + count + stake, f"{count} intruder(s){where}", "boarders", wanted=count + 1)

        for room in player.rooms:
            # Fires and breaches keep doing damage, so they outrank repairs.
            if room.fires:     # one crew member per burning tile
                crew_for_fire = max(1, room.fires // 2) if has_augment(player, "FIRE_EXTINGUISHERS") else room.fires
                # A fire on a system burns it down bar by bar: the more important the system,
                # the sooner the fire must go.
                system = next((n for n, sy in player.systems.items() if sy.room_id == room.room_id), None)
                stake = 0.5 * REPAIR_PRIORITY.get(system, 0.0) if system else 0.0
                where = f" on the {system}" if system else ""
                add(room, 20 + room.fires + stake, f"{room.fires} fire tile(s){where}", "fire", wanted=crew_for_fire)
            if room.breaches:  # one per breach
                add(room, 18 + room.breaches, f"{room.breaches} breach(es)", "breach", wanted=room.breaches)

        low_air = player.oxygen_total is not None and player.oxygen_total < 0.6
        wounded = any(c.health < SEND_TO_MEDBAY * c.max_health for c in self._own_crew(snapshot))
        for system in player.systems.values():
            damaged = system.level - system.health
            room = _room(player.rooms, system.room_id)
            if damaged <= 0 or room is None:
                continue
            importance = REPAIR_PRIORITY.get(system.name, 1.0)
            if system.name == "oxygen" and low_air:
                importance += 6
            if system.name == "medbay" and wounded:
                importance += 3
                if system.health <= 0:
                    importance += 20      # nobody can heal until it works again: before fires
            if system.name == "pilot" and system.health <= 0:
                importance += 4           # no helm: no dodging and no jumping away
            # More hands repair faster: up to one per damaged bar.
            add(room, importance + damaged * 0.1, f"{system.name} damaged ({system.health}/{system.level})",
                "repair", wanted=damaged)

        # A room with several problems (fire on the oxygen system, say) outranks one with a
        # single problem of the same kind: worst problem plus half of the rest.
        return sorted(((best + 0.5 * (total - best), room, ", ".join(notes), job, wanted)
                       for best, room, notes, job, wanted, total in found.values()),
                      key=lambda item: item[0], reverse=True)

    def _damage_control(self, snapshot: GameSnapshot, now: float, safe: dict[str, int] | None = None) -> list[Intent]:
        own_crew = self._own_crew(snapshot)
        hazards = self._hazards(snapshot)
        # Hurt crew rest only if the medbay can heal them; with it broken, waiting heals
        # nobody, so everyone works (repairing the medbay comes first, see _hazards).
        medbay = snapshot.player.systems.get("medbay")
        fit_needed = FIT_FOR_DUTY if medbay is not None and medbay.health > 0 else 0.0
        if safe is not None:
            # Boarding: only rooms that keep their air need crew (fights in the safe rooms,
            # or crew still on their way out); everything else is being vented.
            keep = set(safe.values()) | {c.room_id for c in own_crew}
            keep |= {r for r, plan in self.boarder_plans.items() if plan == "engage"}
            hazards = [h for h in hazards if h[1].room_id in keep]
        hazard_ids = {room.room_id for _, room, _, _, _ in hazards}

        # Release jobs whose room is clear; _return_to_stations then sends the crew home.
        alive = {c.address for c in own_crew}
        for address, (room_id, _) in list(self.assignments.items()):
            if address not in alive or room_id not in hazard_ids:
                del self.assignments[address]

        free = [c for c in own_crew
                if not c.extinguishing and not c.fighting and c.address not in self.assignments
                and c.address not in self.healing and c.health >= fit_needed * c.max_health]
        if safe is not None:
            # The pilot and the door operator hold their posts; fighters come from the medbay.
            held = {safe.get("helm"), safe.get("doors")} - {safe.get("medbay")}
            free = [c for c in free if self.boarding_posts.get(c.address) not in held]
        combat = in_combat(snapshot)

        def pick(room: RoomInfo, priority: float, job: str, helper: bool) -> CrewInfo | None:
            available = [c for c in free if not heading_to(c, room)]   # already there: no order needed
            if job in ("fire", "breach"):   # badly hurt crew would burn or suffocate (Rock do not burn)
                available = [c for c in available if c.health >= FIRE_RETREAT / 2 * c.max_health
                             or (job == "fire" and fire_immune(c))]
            idle = [c for c in available if c.manning < 0 and not c.repairing]
            candidates = idle
            if not candidates:
                if combat and priority < PULL_FROM_STATION_IN_COMBAT:
                    return None  # not worth leaving a station mid-fight
                stations = STATION_SACRIFICE_ORDER + ([] if combat else [PILOT])
                if combat and helper and job != "boarders":
                    # Extra hands mid-fight only come from stations that matter least.
                    stations = STATION_SACRIFICE_ORDER[:HELPER_STATIONS_IN_COMBAT]
                for station in stations:
                    candidates = [c for c in available if c.manning == station]
                    if candidates:
                        break
            if not candidates:
                return None
            ranked = SPECIALISTS.get(job, [])
            specialists = [c for c in candidates if c.species in ranked]
            if specialists:
                best = min(ranked.index(c.species) for c in specialists)
                specialists = [c for c in specialists if ranked.index(c.species) == best]
            return min(specialists or candidates, key=lambda c: _distance(c, room))

        # At most one new order per decision: each order is checked before the next one.
        systems = snapshot.player.systems
        oxygen_room = systems["oxygen"].room_id if "oxygen" in systems else None
        for priority, room, what, job, wanted in hazards:
            if room.room_id in self.vented_rooms:
                continue  # the air is being let out of this room: keep crew away
            if (room.oxygen is not None and room.oxygen < BREATHABLE and job == "fire"
                    and room.room_id != oxygen_room):
                continue  # almost no air: the fire is dying anyway and crew would suffocate
            present = sum(1 for c in own_crew if heading_to(c, room))
            if present >= min(wanted, room_capacity(snapshot, room)):
                continue
            crew = pick(room, priority, job, helper=present > 0)
            if crew is None:
                continue
            if not self._due(("crew_to_room", room.room_id, present), now, REISSUE_CREW_SECONDS):
                continue
            self.assignments[crew.address] = (room.room_id, now)
            team = f" ({present + 1} of {min(wanted, room_capacity(snapshot, room))})" if wanted > 1 else ""
            return [Intent(kind="crew_to_room", crew=crew, room=room,
                           reason=f"room {room.room_id}: {what}; sending {crew.name} ({crew.species}){team}")]
        return []

    def _return_to_stations(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Off-duty crew go back to their station; crew without one take an empty station."""
        crew = [c for c in self._own_crew(snapshot)
                if c.address not in self.assignments and c.address not in self.healing
                and c.address not in getattr(self, "boarding_party", set())
                and not c.fighting and not c.extinguishing and not c.repairing]

        for c in crew:
            station = self.stations.get(c.address)
            room = self._station_room(snapshot, station) if station is not None else None
            if room is None or heading_to(c, room):
                continue
            if self._due(("station", c.address), now, REISSUE_CREW_SECONDS):
                name = SYSTEM_NAMES.get(station, "station")
                return [Intent(kind="crew_to_room", crew=c, room=room,
                               reason=f"{c.name} back to their station ({name}, room {room.room_id})")]

        taken = set(self.stations.values()) | {c.manning for c in self._own_crew(snapshot) if c.manning >= 0}
        open_stations = [s for s in MANNED_STATIONS if s not in taken
                         and any(sys.type_id == s and sys.health > 0 for sys in snapshot.player.systems.values())]
        for c in crew:
            if c.address in self.stations or not open_stations:
                continue
            station = open_stations[0]
            room = self._station_room(snapshot, station)
            if room is None or not self._due(("station", c.address), now, REISSUE_CREW_SECONDS):
                continue
            self.stations[c.address] = station
            return [Intent(kind="crew_to_room", crew=c, room=room,
                           reason=f"{c.name} takes the empty {SYSTEM_NAMES.get(station)} station (room {room.room_id})")]
        return []

    def _emergency(self, snapshot: GameSnapshot, now: float) -> list[Intent]:
        """Jump away from a fight that is lost: hull critical, or a deadlock where the enemy
        hull has not dropped for DEADLOCK_SECONDS (their shields hold, our weapons are down).
        Not while our boarders are on the enemy ship, and not when the enemy is nearly dead.
        While wanting to leave, the power plan puts the engines first (self.escaping)."""
        player, enemy = snapshot.player, snapshot.enemy
        if not in_combat(snapshot) or enemy is None:
            self.escaping, self.fight_track = False, None
            return []
        track = getattr(self, "fight_track", None)
        if track is None or track["enemy"] != enemy.blueprint:
            track = self.fight_track = {"enemy": enemy.blueprint, "hull": enemy.hull, "since": now, "start": now}
        if enemy.hull < track["hull"]:
            track["hull"], track["since"] = enemy.hull, now
        # While the enemy is cloaked our guns hold their fire: that time is not a deadlock.
        cloak = enemy.systems.get("cloaking")
        if cloak is not None and cloak.special.get("on"):
            track["since"] += now - track.get("last", now)
        track["last"] = now
        critical = player.hull <= max(4, player.max_hull // 4)
        # The Flagship has to be beaten here, and its 4 shield layers can hold a while: no
        # deadlock escape from it, only the critical-hull one (to repair and come back).
        stalled = now - track["since"] > DEADLOCK_SECONDS and not flagship.is_flagship(enemy)
        nearly_won = enemy.hull <= max(2, enemy.max_hull // 4)
        away = [c for c in enemy.crew if c.intruder and not c.dead and not c.mind_controlled]
        want = (critical and not (nearly_won and player.hull > 3)) or (stalled and not nearly_won)
        self.escaping = want and not away
        if not self.escaping:
            return []
        why = (f"hull {player.hull}/{player.max_hull}" if critical
               else f"no damage dealt for {now - track['since']:.0f}s (enemy hull {enemy.hull}/{enemy.max_hull})")
        engines = player.systems.get("engines")
        if not player.jump_ready or engines is None or engines.power <= 0:
            if self._due(("escape", "prep"), now, 10.0):
                return [Intent(kind="alert", reason=f"preparing to jump away ({why}): powering engines, waiting for the FTL drive")]
            return []
        if not self._due(("escape",), now, REISSUE_TARGET_SECONDS * 5):
            return []
        return [Intent(kind="escape", reason=f"{why}: the fight is not winnable, jumping away")]
