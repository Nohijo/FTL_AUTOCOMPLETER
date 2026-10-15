"""Turn FTL's memory into a plain snapshot of everything the bot needs."""

import math
import struct
from dataclasses import asdict, dataclass, field

from observer.memory import layout as L
from observer.memory.blueprints import weapon_stats
from observer.memory.process import FTLProcess, MemoryReadError


@dataclass
class SystemInfo:
    type_id: int
    name: str
    raw_name: str
    power: int                        # bars currently powered
    level: int                        # bars the system has (its upgrade level)
    health: int                       # undamaged bars
    max_level: int                    # upgrade cap
    room_id: int
    manned: bool
    on_fire: bool
    breached: bool
    ion_lock: int
    battery_power: int
    hacked: int
    address: int = 0
    special: dict = field(default_factory=dict)   # hacking / teleporter / mind state (see _special_state)


@dataclass
class WeaponInfo:
    blueprint: str
    weapon_type: str
    powered: bool
    charge: float
    charge_goal: float
    ready: bool
    required_power: int
    damage: int
    shots: int
    missiles_per_shot: int
    auto_firing: bool
    targeted: bool
    stats: dict = field(default_factory=dict)   # observer/memory/blueprints.weapon_stats


@dataclass
class ArtilleryInfo:
    """One artillery system: a Flagship weapon pod (each in its own room) or a cruiser's artillery."""
    room_id: int
    level: int
    health: int
    power: int
    weapon: WeaponInfo | None


@dataclass
class DroneInfo:
    blueprint: str                    # e.g. "DEFENSE_1"
    kind: str                         # typeName, e.g. "DEFENSE", "COMBAT", "REPAIR"
    powered: bool
    deployed: bool
    dead: bool
    power: int = 1                    # DroneBlueprint.power: reactor bars it needs

    @property
    def active(self) -> bool:
        return self.powered and self.deployed and not self.dead


@dataclass
class CrewInfo:
    address: int
    name: str
    species: str
    health: float
    max_health: float
    room_id: int
    x: float
    y: float
    goal_x: float
    goal_y: float
    slot_room: int                    # room the crew member is assigned to (currentSlot.roomId)
    new_path: bool                    # an order is queued but not walked yet (e.g. while paused)
    ship_id: int
    manning: int                      # system type being manned, -1 if none
    repairing: bool
    extinguishing: bool
    intruder: bool
    fighting: bool
    dead: bool
    mind_controlled: bool


@dataclass
class DoorInfo:
    address: int
    room1: int
    room2: int                        # -1 for an airlock
    open: bool
    x: int                            # door centre, ship space (like room rects)
    y: int

    @property
    def airlock(self) -> bool:
        return self.room1 < 0 or self.room2 < 0

    def other_side(self, room_id: int) -> int:
        return self.room2 if self.room1 == room_id else self.room1


@dataclass
class RoomInfo:
    room_id: int
    x: int
    y: int
    w: int
    h: int
    oxygen: float | None
    fires: int
    breaches: int


@dataclass
class ShipInfo:
    ship_id: int
    blueprint: str
    name: str
    is_player: bool
    hull: int
    max_hull: int
    shield_layers: int
    max_shield_layers: int
    shield_charge: float
    super_shield: int
    fuel: int
    missiles: int
    drone_parts: int
    scrap: int
    jump_charge: float
    jump_goal: float
    reactor_used: int | None
    reactor_total: int | None
    oxygen_total: float | None
    hostile: bool
    targetable_hostile: bool
    destroyed: bool
    jumping: bool
    jump_animating: bool
    surrendered: bool
    escaping: bool
    systems: dict[str, SystemInfo] = field(default_factory=dict)
    weapons: list[WeaponInfo] = field(default_factory=list)
    drones: list[DroneInfo] = field(default_factory=list)
    crew: list[CrewInfo] = field(default_factory=list)
    rooms: list[RoomInfo] = field(default_factory=list)
    doors: list[DoorInfo] = field(default_factory=list)
    augments: dict[str, int] = field(default_factory=dict)   # augment blueprint name -> count
    artillery: list[ArtilleryInfo] = field(default_factory=list)   # every artillery system (systems[] keeps one)
    battery_used: int = 0             # Backup Battery bars in use / available while it is on
    battery_total: int = 0
    boss_stage: int = 0               # the Flagship: 1..3 (0 = not the Flagship)
    surge_in: float | None = None     # Flagship: seconds until its next power surge
    surge_count: int = 0              # Flagship: surges so far (stage 3: count % 4 == 3 is the Zoltan shield)

    @property
    def jump_ready(self) -> bool:
        return self.jump_goal > 0 and self.jump_charge >= self.jump_goal


@dataclass
class EventChoice:
    choice_type: int
    text: str
    scrap: int
    fuel: int
    missiles: int
    drones: int
    crew: int


@dataclass
class ScreenMapping:
    """How game coordinates (1280x720) map onto the FTL window."""
    modifier: tuple[int, int] = (0, 0)
    mouse_modifier: tuple[float, float] = (1.0, 1.0)
    bar: tuple[int, int] = (0, 0)

    def game_to_window(self, x: float, y: float) -> tuple[int, int]:
        # Inverse of CApp::OnMouseMove: game = int((window - bar) * mouseModifier) - modifier.
        # FTL truncates, so take the smallest window pixel that maps back onto round(x), round(y).
        def axis(value: float, modifier: int, scale: float, bar: int) -> int:
            return math.ceil((round(value) + modifier) / scale - 1e-4) + bar
        return (axis(x, self.modifier[0], self.mouse_modifier[0], self.bar[0]),
                axis(y, self.modifier[1], self.mouse_modifier[1], self.bar[1]))


@dataclass
class GameSnapshot:
    screen: str                       # "main_menu", "loading", "in_run", "no_game"
    focused: bool = False
    mapping: ScreenMapping = field(default_factory=ScreenMapping)
    player_origin: tuple[int, int] | None = None   # game coords of the player ship's rect origin
    enemy_origin: tuple[int, int] | None = None    # game coords of the enemy ship's rect origin
    combat_open: bool = False
    crew_hotkeys: dict[int, int] = field(default_factory=dict)  # CrewMember address -> N in hotkey "crewN"
    selected_crew: list[int] = field(default_factory=list)     # addresses of selected crew
    paused: bool = False
    auto_paused: bool = False
    focus_paused: bool = False        # paused by FTL itself when it thought it lost focus
    pause_flag: bool = False          # CommandGui.bPaused: the space-bar pause, also set by CApp::OnInputBlur
    menu_open: bool = False
    event_open: bool = False
    event_text: str = ""
    event_choices: list[EventChoice] = field(default_factory=list)
    store_open: bool = False
    store_button: tuple[int, int, int, int] | None = None   # HUD store button when a store is here
    ship_screen_open: bool = False
    map_open: bool = False
    game_over: bool = False
    sector: int | None = None
    nebula: bool = False
    storm: bool = False
    beacon_hazards: list[str] = field(default_factory=list)   # sun, pulsar, anti-ship battery, ...
    location_event: str = ""
    location_has_store: bool = False
    player: ShipInfo | None = None
    enemy: ShipInfo | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --- helpers -----------------------------------------------------------------

def _safe(errors: list[str], label: str, func, default=None):
    try:
        return func()
    except (MemoryReadError, struct.error, UnicodeDecodeError, IndexError) as error:
        errors.append(f"{label}: {error}")
        return default


def _read_power_manager(proc: FTLProcess, ship_id: int) -> tuple[int, int] | tuple[None, None]:
    begin, count = proc.vector_range(L.POWER_MANAGERS, L.POWER_MANAGER_SIZE)
    if ship_id < 0 or ship_id >= count:
        return None, None
    view = proc.view(begin + ship_id * L.POWER_MANAGER_SIZE, L.POWER_MANAGER_SIZE)
    used, total = view.pair_i(L.PowerManager.CURRENT_POWER)
    # What the reactor can really give now: an ion storm divides it (iTempDividePower),
    # events can cap it (iTempPowerCap) or take bars away (iTempPowerLoss).   DWARF
    cap, loss, divide = view.i32(0x14), view.i32(0x18), view.i32(0x1C)
    usable = total // divide if divide > 1 else total
    usable = max(0, min(usable, cap if cap > 0 else usable) - max(0, loss))
    return used, usable


def _read_battery_power(proc: FTLProcess, ship_id: int) -> tuple[int, int]:
    """PowerManager.batteryPower: (bars in use, bars available) while the Backup Battery is on."""
    begin, count = proc.vector_range(L.POWER_MANAGERS, L.POWER_MANAGER_SIZE)
    if ship_id < 0 or ship_id >= count:
        return 0, 0
    return proc.view(begin + ship_id * L.POWER_MANAGER_SIZE, L.POWER_MANAGER_SIZE).pair_i(L.PowerManager.BATTERY_POWER)


def _read_boss(proc: FTLProcess, complete_ship: int, ship: ShipInfo):
    """The Flagship (a BossShip): its stage, and when BossShip::OnLoop fires the next surge
    (stage 2: PrepareSuperDrones; stage 3: PrepareSuperBarrage, every 4th PowerSuperShields)."""
    view = proc.view(complete_ship, L.BossShip.DEATH_BEGAN + 8)
    stage = view.i32(L.BossShip.CURRENT_STAGE)
    if not 1 <= stage <= 3:
        return
    ship.boss_stage = stage
    timer = L.BossShip.POWER_TIMER
    time, goal = view.f32(timer + L.TimerHelper.CURR_TIME), view.f32(timer + L.TimerHelper.CURR_GOAL)
    if view.bool(timer + L.TimerHelper.RUNNING) and goal > 0:
        ship.surge_in = max(0.0, goal - time)
    ship.surge_count = view.i32(L.BossShip.POWER_COUNT)


def _system_read_size(type_id: int) -> int:
    return {
        L.SYS_SHIELDS: L.Shields.SIZE,
        L.SYS_WEAPONS: L.WeaponSystem.SIZE,
        L.SYS_DRONES: L.DroneSystem.SIZE,
        L.SYS_OXYGEN: L.OxygenSystem.SIZE,
    }.get(type_id, L.ShipSystem.SIZE)


def _read_system(proc: FTLProcess, address: int) -> tuple[SystemInfo, "View"]:
    type_id = proc.i32(address + L.ShipSystem.TYPE)
    view = proc.view(address, _system_read_size(type_id))
    power, level = view.pair_i(L.ShipSystem.POWER)
    health, _ = view.pair_i(L.ShipSystem.HEALTH)
    info = SystemInfo(
        type_id=type_id,
        name=L.SYSTEM_NAMES.get(type_id, f"system_{type_id}"),
        raw_name=proc.string_at(view.ptr(L.ShipSystem.NAME)),
        power=power,
        level=level,
        health=health,
        max_level=view.i32(L.ShipSystem.MAX_LEVEL),
        room_id=view.i32(L.ShipSystem.ROOM_ID),
        manned=view.bool(L.ShipSystem.MANNED),
        on_fire=view.bool(L.ShipSystem.ON_FIRE),
        breached=view.bool(L.ShipSystem.BREACHED),
        ion_lock=view.i32(L.ShipSystem.LOCK_COUNT),
        battery_power=view.i32(L.ShipSystem.BATTERY_POWER),
        hacked=view.i32(L.ShipSystem.HACK_EFFECT),
        address=address,
    )
    info.special = _special_state(proc, type_id, address)
    return info, view


def _special_state(proc: FTLProcess, type_id: int, address: int) -> dict:
    """State of the systems the bot drives with a target click (DWARF offsets)."""
    try:
        if type_id == 15:                                 # HackingSystem
            data = proc.read(address + 0x244, 0x610)
            at = lambda off: data[off - 0x244]
            return {"active": bool(at(0x244)), "armed": bool(at(0x821)),
                    "targeted": struct.unpack_from("<Q", data, 0x828 - 0x244)[0] != 0,
                    "can_hack": bool(at(0x838)), "drone_arrived": bool(at(0x248 + 0x368))}
        if type_id == 9:                                  # TeleportSystem
            data = proc.read(address + 0x244, 0x40)
            return {"charge": struct.unpack_from("<f", data, 0)[0], "can_send": bool(data[4]),
                    "can_receive": bool(data[5]), "armed": struct.unpack_from("<i", data, 8)[0],
                    "slots": struct.unpack_from("<i", data, 0x27C - 0x244)[0]}
        if type_id in (L.SYS_CLOAKING, L.SYS_BATTERY):    # bTurnedOn + how long it stays on
            data = proc.read(address + L.ToggleSystem.TURNED_ON, 0x18)
            timer = L.ToggleSystem.TIMER - L.ToggleSystem.TURNED_ON
            time, goal = struct.unpack_from("<ff", data, timer + L.TimerHelper.CURR_TIME)
            return {"on": bool(data[0]), "time": time, "goal": goal}
        if type_id == 14:                                 # MindSystem
            data = proc.read(address + 0x244, 0x30)
            begin, end = struct.unpack_from("<QQ", data, 0x258 - 0x244)
            return {"can_use": bool(data[8]), "armed": struct.unpack_from("<i", data, 0xC)[0],
                    "controlled": (end - begin) // 8}
    except Exception:
        pass
    return {}


def _read_weapon(proc: FTLProcess, address: int) -> WeaponInfo:
    view = proc.view(address, L.ProjectileFactory.SIZE)
    charge, goal = view.pair_f(L.ProjectileFactory.COOLDOWN)
    blueprint_address = view.ptr(L.ProjectileFactory.BLUEPRINT)

    stats = weapon_stats(proc, blueprint_address) if blueprint_address else {}
    blueprint_name, weapon_type = stats.get("name", ""), stats.get("type", "")
    damage, shots, missiles = stats.get("damage", 0), stats.get("shots", 0), stats.get("missiles", 0)

    _, target_count = proc.vector_range(address + L.ProjectileFactory.TARGETS, 8)
    powered = view.bool(L.ProjectileFactory.POWERED)

    return WeaponInfo(
        blueprint=blueprint_name,
        weapon_type=weapon_type,
        powered=powered,
        charge=charge,
        charge_goal=goal,
        ready=powered and goal > 0 and charge >= goal,
        required_power=view.i32(L.ProjectileFactory.REQUIRED_POWER),
        damage=damage,
        shots=shots,
        missiles_per_shot=missiles,
        auto_firing=view.bool(L.ProjectileFactory.AUTO_FIRING),
        targeted=target_count > 0,
        stats=stats,
    )


def _read_drone(proc: FTLProcess, address: int) -> DroneInfo:
    view = proc.view(address, L.Drone.SIZE)
    blueprint = view.ptr(L.Drone.BLUEPRINT)
    return DroneInfo(
        blueprint=proc.string(blueprint + L.Blueprint.NAME) if blueprint else "",
        kind=proc.string(blueprint + L.DRONE_BLUEPRINT_TYPE_NAME) if blueprint else "",
        powered=view.bool(L.Drone.POWERED),
        deployed=view.bool(L.Drone.DEPLOYED),
        dead=view.bool(L.Drone.DEAD),
        power=proc.i32(blueprint + L.DroneBlueprint.POWER) if blueprint else 1,
    )


def _read_crew(proc: FTLProcess, address: int) -> CrewInfo:
    view = proc.view(address, L.CrewMember.SIZE)
    health, max_health = view.pair_f(L.CrewMember.HEALTH)
    # Same tests as CrewMember::Repairing() / RepairingFire() in the game.
    fighting = view.bool(L.CrewMember.FIGHTING)
    has_repair_target = view.ptr(L.CrewMember.CURRENT_REPAIR) != 0
    extinguishing = (has_repair_target and view.i32(L.CrewMember.ON_FIRE) > 0
                     and not view.bool(L.CrewMember.FROZEN) and not fighting)
    return CrewInfo(
        address=address,
        name=proc.string_at(view.ptr(L.CrewMember.CREW_NAME)),
        species=proc.string_at(view.ptr(L.CrewMember.SPECIES)),
        health=health,
        max_health=max_health,
        room_id=view.i32(L.CrewMember.ROOM_ID),
        x=view.f32(L.CrewMember.X),
        y=view.f32(L.CrewMember.Y),
        goal_x=view.f32(L.CrewMember.GOAL_X),
        goal_y=view.f32(L.CrewMember.GOAL_Y),
        slot_room=view.i32(L.CrewMember.SLOT_ROOM),
        new_path=view.bool(L.CrewMember.NEW_PATH),
        ship_id=view.i32(L.CrewMember.SHIP_ID),
        manning=view.i32(L.CrewMember.MANNING_ID),
        repairing=has_repair_target and not fighting and not extinguishing,
        extinguishing=extinguishing,
        intruder=view.bool(L.CrewMember.INTRUDER),
        fighting=fighting,
        dead=view.bool(L.CrewMember.DEAD),
        mind_controlled=view.bool(L.CrewMember.MIND_CONTROLLED),
    )


def _burning_tiles(proc: FTLProcess, ship_manager: int) -> set[tuple[int, int]]:
    """Tiles whose Fire has fDamage > 0, like Spreader<Fire>::CountInRoom."""
    grid = ship_manager + L.FireSpreader.OFFSET_IN_SHIP_MANAGER + L.FireSpreader.GRID
    begin, columns = proc.vector_range(grid, 24)
    burning = set()
    for x in range(columns):
        column_begin, rows = proc.vector_range(begin + x * 24, L.FireSpreader.FIRE_SIZE)
        if rows == 0:
            continue
        data = proc.read(column_begin, rows * L.FireSpreader.FIRE_SIZE)
        for y in range(rows):
            damage = struct.unpack_from("<f", data, y * L.FireSpreader.FIRE_SIZE + L.Repairable.DAMAGE)[0]
            if damage > 0:
                burning.add((x, y))
    return burning


def _breach_points(proc: FTLProcess, ship_manager: int) -> list[tuple[int, int]]:
    """Points of damaged outer walls, offset like Ship::ContainsHullBreach does."""
    points = []
    for address in proc.pointer_vector(ship_manager + L.ShipManager.SHIP + L.Ship.OUTER_WALLS):
        view = proc.view(address, 0x28)
        if view.f32(L.Repairable.DAMAGE) > 0:
            x, y = view.pair_i(L.Repairable.LOCATION)
            points.append((x + 17, y + 17))
    return points


def read_augments(proc: FTLProcess, ship_id: int) -> dict[str, int]:
    """ShipObject::shipInfoList[ship_id].augList: a std::map<string,int> (red-black tree;
    header at +0x8: parent +0x10; node: left +0x10, right +0x18, key string +0x20, int +0x28)."""
    begin, end = struct.unpack("<QQ", proc.read(L.SHIP_INFO_LIST, 16))
    if ship_id < 0 or begin + (ship_id + 1) * L.SHIP_INFO_SIZE > end:
        return {}
    info = begin + ship_id * L.SHIP_INFO_SIZE
    found: dict[str, int] = {}
    stack = [proc.ptr(info + 0x8 + 0x8)]       # root = header._M_parent
    while stack and len(found) < 16:
        node = stack.pop()
        if node == 0:
            continue
        view = proc.view(node, 0x30)
        found[proc.string_at(view.ptr(0x20))] = view.i32(0x28)
        stack += [view.ptr(0x10), view.ptr(0x18)]
    return found


def _read_rooms(proc: FTLProcess, ship_manager: int, oxygen_levels: list[float],
                errors: list[str], label: str) -> list[RoomInfo]:
    burning = _safe(errors, f"{label}.fires", lambda: _burning_tiles(proc, ship_manager), set())
    breaches = _safe(errors, f"{label}.breaches", lambda: _breach_points(proc, ship_manager), [])
    tile = L.TILE_SIZE

    rooms = []
    for address in proc.pointer_vector(ship_manager + L.ShipManager.SHIP + L.Ship.ROOM_LIST):
        view = proc.view(address, L.Room.READ_SIZE)
        x, y, w, h = struct.unpack_from("<iiii", view.data, L.Room.RECT)
        room_id = view.i32(L.Room.ROOM_ID)
        tiles = {(tx, ty) for tx in range(x // tile, (x + w) // tile)
                 for ty in range(y // tile, (y + h) // tile)}
        rooms.append(RoomInfo(
            room_id=room_id,
            x=x, y=y, w=w, h=h,
            oxygen=oxygen_levels[room_id] if 0 <= room_id < len(oxygen_levels) else None,
            fires=len(tiles & burning),
            breaches=sum(1 for bx, by in breaches if x <= bx < x + w and y <= by < y + h),
        ))
    return rooms


def read_ship(proc: FTLProcess, complete_ship: int, errors: list[str]) -> ShipInfo | None:
    cs = proc.view(complete_ship, L.CompleteShip.SIZE)
    sm_address = cs.ptr(L.CompleteShip.SHIP_MANAGER)
    if sm_address == 0:
        return None

    sm = proc.view(sm_address, L.ShipManager.SIZE)
    ship_id = cs.i32(L.CompleteShip.SHIP_ID)
    label = "player" if cs.bool(L.CompleteShip.IS_PLAYER) else "enemy"

    hull, max_hull = sm.pair_i(L.ShipManager.SHIP + L.Ship.HULL)
    jump_charge, jump_goal = sm.pair_f(L.ShipManager.JUMP_TIMER)
    reactor_used, reactor_total = _safe(
        errors, f"{label}.reactor", lambda: _read_power_manager(proc, ship_id), (None, None)
    )

    ship = ShipInfo(
        ship_id=ship_id,
        blueprint=_safe(errors, f"{label}.blueprint",
                        lambda: proc.string_at(sm.ptr(L.ShipManager.BLUEPRINT + L.ShipBlueprint.BLUEPRINT_NAME)), ""),
        name=_safe(errors, f"{label}.name",
                   lambda: proc.string_at(sm.ptr(L.ShipManager.SHIP + L.Ship.SHIP_NAME)), ""),
        is_player=cs.bool(L.CompleteShip.IS_PLAYER),
        hull=hull,
        max_hull=max_hull,
        shield_layers=0,
        max_shield_layers=0,
        shield_charge=0.0,
        super_shield=0,
        fuel=sm.i32(L.ShipManager.FUEL),
        missiles=sm.i32(L.ShipManager.TEMP_MISSILE_COUNT),
        drone_parts=sm.i32(L.ShipManager.TEMP_DRONE_COUNT),
        scrap=sm.i32(L.ShipManager.SCRAP),
        jump_charge=jump_charge,
        jump_goal=jump_goal,
        reactor_used=reactor_used,
        reactor_total=reactor_total,
        oxygen_total=None,
        hostile=sm.bool(L.ShipManager.HOSTILE),
        targetable_hostile=sm.bool(L.ShipManager.TARGETABLE_HOSTILE),
        destroyed=sm.bool(L.ShipManager.DESTROYED),
        jumping=sm.bool(L.ShipManager.JUMPING),
        jump_animating=sm.bool(L.ShipManager.JUMP_ANIMATING),
        surrendered=cs.bool(L.CompleteShip.AI_SURRENDERED),
        escaping=cs.bool(L.CompleteShip.AI_ESCAPING),
    )

    oxygen_levels: list[float] = []
    system_addresses = _safe(errors, f"{label}.systems",
                             lambda: proc.pointer_vector(sm_address + L.ShipManager.SYSTEM_LIST), [])

    for address in system_addresses:
        result = _safe(errors, f"{label}.system@{address:x}", lambda: _read_system(proc, address))
        if result is None:
            continue
        info, view = result
        ship.systems[info.name] = info

        if info.type_id == L.SYS_SHIELDS:
            ship.shield_charge = view.f32(L.Shields.CHARGER)
            ship.shield_layers, ship.max_shield_layers = view.pair_i(L.Shields.LAYERS)
            ship.super_shield = view.i32(L.Shields.SUPER)
        elif info.type_id == L.SYS_WEAPONS:
            ship.missiles = view.i32(L.WeaponSystem.MISSILE_COUNT)
            weapon_addresses = _safe(errors, f"{label}.weapons",
                                     lambda: proc.pointer_vector(address + L.WeaponSystem.WEAPONS), [])
            for weapon_address in weapon_addresses:
                weapon = _safe(errors, f"{label}.weapon@{weapon_address:x}",
                               lambda: _read_weapon(proc, weapon_address))
                if weapon is not None:
                    ship.weapons.append(weapon)
        elif info.type_id == L.SYS_DRONES:
            ship.drone_parts = view.i32(L.DroneSystem.DRONE_COUNT)
            for drone_address in _safe(errors, f"{label}.drones",
                                       lambda: proc.pointer_vector(address + L.DroneSystem.DRONES), []):
                drone = _safe(errors, f"{label}.drone", lambda: _read_drone(proc, drone_address))
                if drone is not None:
                    ship.drones.append(drone)
        elif info.type_id == L.SYS_ARTILLERY:
            factory = _safe(errors, f"{label}.artillery", lambda: proc.ptr(address + L.ArtillerySystem.PROJECTILE_FACTORY), 0)
            weapon = _safe(errors, f"{label}.artillery.weapon", lambda: _read_weapon(proc, factory)) if factory else None
            ship.artillery.append(ArtilleryInfo(room_id=info.room_id, level=info.level, health=info.health,
                                                power=info.power, weapon=weapon))
        elif info.type_id == L.SYS_OXYGEN:
            ship.oxygen_total = view.f32(L.OxygenSystem.TOTAL)
            oxygen_levels = _safe(errors, f"{label}.oxygen",
                                  lambda: proc.float_vector(address + L.OxygenSystem.LEVELS), [])

    for crew_address in _safe(errors, f"{label}.crew",
                              lambda: proc.pointer_vector(sm_address + L.ShipManager.CREW_LIST), []):
        crew = _safe(errors, f"{label}.crew@{crew_address:x}", lambda: _read_crew(proc, crew_address))
        if crew is not None:
            ship.crew.append(crew)

    ship.augments = _safe(errors, f"{label}.augments", lambda: read_augments(proc, ship.ship_id), {})
    ship.battery_used, ship.battery_total = _safe(errors, f"{label}.battery",
                                                  lambda: _read_battery_power(proc, ship_id), (0, 0))
    if not ship.is_player and ship.blueprint.startswith("BOSS_"):
        _safe(errors, "boss", lambda: _read_boss(proc, complete_ship, ship))
    ship.rooms = _safe(errors, f"{label}.rooms",
                       lambda: _read_rooms(proc, sm_address, oxygen_levels, errors, label), [])
    if ship.is_player:
        ship.doors = _safe(errors, f"{label}.doors", lambda: _read_doors(proc, sm_address), [])
    return ship


def _read_doors(proc: FTLProcess, ship_manager: int) -> list[DoorInfo]:
    doors = []
    for offset in (L.Ship.DOOR_LIST, L.Ship.AIRLOCKS):
        for address in proc.pointer_vector(ship_manager + L.ShipManager.SHIP + offset):
            view = proc.view(address, L.Door.SIZE)
            doors.append(DoorInfo(
                address=address,
                room1=view.i32(L.Door.ROOM1),
                room2=view.i32(L.Door.ROOM2),
                open=view.bool(L.Door.OPEN),
                x=view.i32(L.Door.X),
                y=view.i32(L.Door.Y),
            ))
    return doors


def _read_event(proc: FTLProcess, gui: int, snapshot: GameSnapshot):
    box = gui + L.CommandGui.CHOICE_BOX
    snapshot.event_text = proc.string(box + L.ChoiceBox.MAIN_TEXT)
    begin, count = proc.vector_range(box + L.ChoiceBox.CHOICES, L.ChoiceText.SIZE)
    for index in range(count):
        item = proc.view(begin + index * L.ChoiceText.SIZE, L.ChoiceText.SIZE)
        rewards = L.ChoiceText.REWARDS
        snapshot.event_choices.append(EventChoice(
            choice_type=item.i32(L.ChoiceText.TYPE),
            text=proc.string_at(item.ptr(L.ChoiceText.TEXT)),
            missiles=item.i32(rewards + 0x0),
            fuel=item.i32(rewards + 0x4),
            drones=item.i32(rewards + 0x8),
            scrap=item.i32(rewards + 0xC),
            crew=item.i32(rewards + 0x10),
        ))


def weapon_armed(proc: FTLProcess) -> bool:
    """True while a weapon is selected for aiming (WeaponControl.armedWeapon is set)."""
    gui = proc.ptr(proc.ptr(L.P_THE_APP) + L.CApp.GUI)
    return proc.ptr(gui + L.CommandGui.COMBAT_CONTROL + L.CombatControl.ARMED_WEAPON) != 0


def read_snapshot(proc: FTLProcess) -> GameSnapshot:
    app = proc.ptr(L.P_THE_APP)
    if app == 0:
        return GameSnapshot(screen="no_game")

    app_view = proc.view(app, L.CApp.SIZE)
    world = app_view.ptr(L.CApp.WORLD)
    gui = app_view.ptr(L.CApp.GUI)
    common = dict(
        focused=app_view.bool(L.CApp.FOCUS),
        mapping=ScreenMapping(
            modifier=app_view.pair_i(L.CApp.MODIFIER),
            mouse_modifier=app_view.pair_f(L.CApp.MOUSE_MODIFIER),
            bar=app_view.pair_i(L.CApp.BAR),
        ),
    )

    if app_view.bool(L.CApp.MENU):
        return GameSnapshot(screen="main_menu", **common)
    if world == 0 or gui == 0:
        return GameSnapshot(screen="no_game", **common)

    world_view = proc.view(world, 0x1530)
    if world_view.bool(L.WorldManager.LOADING_GAME) or not world_view.bool(L.WorldManager.STARTED_GAME):
        return GameSnapshot(screen="loading", **common)

    snapshot = GameSnapshot(screen="in_run", **common)
    errors = snapshot.errors
    space = L.WorldManager.SPACE

    gui_view = proc.view(gui, L.CommandGui.SIZE)
    combat = L.CommandGui.COMBAT_CONTROL
    snapshot.player_origin = gui_view.pair_i(L.CommandGui.SHIP_POSITION)
    snapshot.combat_open = gui_view.bool(combat + L.CombatControl.OPEN)
    if gui_view.ptr(combat + L.CombatControl.CURRENT_TARGET):
        px, py = gui_view.pair_i(combat + L.CombatControl.POSITION)
        tx, ty = gui_view.pair_i(combat + L.CombatControl.TARGET_POSITION)
        snapshot.enemy_origin = (px + tx, py + ty)
    snapshot.paused = gui_view.bool(L.CommandGui.PAUSED) or world_view.bool(space + L.SpaceManager.GAME_PAUSED)
    snapshot.auto_paused = gui_view.bool(L.CommandGui.AUTO_PAUSED)
    snapshot.pause_flag = gui_view.bool(L.CommandGui.PAUSED)
    snapshot.menu_open = gui_view.bool(L.CommandGui.MENU_BOX + L.FocusWindow.OPEN)
    # CApp::OnInputBlur pauses through the menu-pause flag; with no menu actually open,
    # that pause is FTL's own focus-loss pause, not one the player chose.
    snapshot.focus_paused = (gui_view.bool(L.CommandGui.PAUSED) and gui_view.bool(L.CommandGui.MENU_PAUSE)
                             and not snapshot.menu_open)
    snapshot.event_open = gui_view.bool(L.CommandGui.CHOICE_BOX + L.FocusWindow.OPEN)
    snapshot.store_open = gui_view.bool(L.CommandGui.STORE_SCREENS + L.FocusWindow.OPEN)
    if gui_view.bool(L.CommandGui.STORE_BUTTON + L.GenericButton.ACTIVE):
        snapshot.store_button = struct.unpack_from(
            "<iiii", gui_view.data, L.CommandGui.STORE_BUTTON + L.GenericButton.HITBOX)
    snapshot.ship_screen_open = gui_view.bool(L.CommandGui.SHIP_SCREENS + L.FocusWindow.OPEN)
    snapshot.game_over = (gui_view.bool(L.CommandGui.GAME_OVER)
                          or gui_view.bool(L.CommandGui.GAME_OVER_SCREEN + L.FocusWindow.OPEN))
    snapshot.nebula = world_view.bool(space + L.SpaceManager.NEBULA)
    snapshot.storm = world_view.bool(space + L.SpaceManager.STORM)
    for name, offset in (("solar flares", L.SpaceManager.SUN), ("pulsar", L.SpaceManager.PULSAR),
                         ("anti-ship battery", L.SpaceManager.PDS), ("ion storm", L.SpaceManager.STORM),
                         ("asteroids", L.SpaceManager.ASTEROIDS_RUNNING)):
        if world_view.bool(space + offset):
            snapshot.beacon_hazards.append(name)

    star_map = world + L.WorldManager.STAR_MAP
    snapshot.map_open = _safe(errors, "map", lambda: proc.bool(star_map + L.FocusWindow.OPEN), False)
    snapshot.sector = _safe(errors, "sector", lambda: proc.i32(star_map + L.StarMap.WORLD_LEVEL) + 1)

    location = _safe(errors, "location", lambda: proc.ptr(star_map + L.StarMap.CURRENT_LOC), 0)
    if location:
        event = _safe(errors, "location.event", lambda: proc.ptr(location + L.Location.EVENT), 0)
        if event:
            snapshot.location_has_store = _safe(errors, "event.store",
                                                lambda: proc.bool(event + L.LocationEvent.STORE), False)
            snapshot.location_event = _safe(errors, "event.name",
                                            lambda: proc.string(event + L.LocationEvent.EVENT_NAME), "")

    if snapshot.event_open:
        _safe(errors, "event", lambda: _read_event(proc, gui, snapshot))

    crew_control = gui + L.CommandGui.CREW_CONTROL
    snapshot.selected_crew = _safe(errors, "crew.selected",
                                   lambda: proc.pointer_vector(crew_control + L.CrewControl.SELECTED_CREW), [])
    for box_address in _safe(errors, "crew.boxes",
                             lambda: proc.pointer_vector(crew_control + L.CrewControl.CREW_BOXES), []):
        box = _safe(errors, "crew.box", lambda: proc.view(box_address, L.CrewBox.READ_SIZE))
        if box is not None and box.bool(L.CrewBox.SELECTABLE):
            snapshot.crew_hotkeys[box.ptr(L.CrewBox.CREW)] = box.i32(L.CrewBox.NUMBER) + 1

    player_cs = world_view.ptr(L.WorldManager.PLAYER_SHIP)
    if player_cs:
        snapshot.player = _safe(errors, "player", lambda: read_ship(proc, player_cs, errors))
        enemy_cs = _safe(errors, "enemy.ptr", lambda: proc.ptr(player_cs + L.CompleteShip.ENEMY_SHIP), 0)
        if enemy_cs:
            snapshot.enemy = _safe(errors, "enemy", lambda: read_ship(proc, enemy_cs, errors))

    return snapshot
