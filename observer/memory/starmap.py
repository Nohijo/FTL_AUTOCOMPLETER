"""Read the sector map: beacons, their events, the rebel fleet and the next-sector choices."""

import math
import struct
from dataclasses import dataclass, field

from observer.memory import layout as L
from observer.memory.process import FTLProcess

M = L.StarMap


@dataclass
class Beacon:
    address: int
    x: float
    y: float
    connected: list[int]
    known: bool
    visited: bool
    exit: bool
    overtaken: bool                   # inside the rebel fleet circle
    nebula: bool
    store: bool
    event: int                        # LocationEvent* (0 if none)
    event_name: str


@dataclass
class SectorOption:
    index: int                        # position in the sector map's hit boxes
    kind: str                         # e.g. "CIVILIAN", "HOSTILE", "NEBULA"
    name: str
    rect: tuple[int, int, int, int]   # screen game coords (x, y, w, h)


@dataclass
class StarMapState:
    open: bool
    choosing_sector: bool
    origin: tuple[float, float]       # screen position of Location.loc (0, 0)
    current: int
    hover: int
    potential: int
    beacons: dict[int, Beacon]
    fleet: tuple[int, int]
    fleet_radius: float
    sector: int                       # 1-based
    end_button: tuple[int, int, int, int] | None   # screen game coords, when active
    sector_options: list[SectorOption] = field(default_factory=list)
    pursuit_delay: int = 0
    nebula_map: bool = False
    wait_button: tuple | None = None        # (x, y, w, h) screen game coords, when shown (out of fuel)
    distress_button: tuple | None = None
    distress_on: bool = False
    out_of_fuel: bool = False
    # Sector 8: the Flagship moves along boss_path, one beacon every second jump of ours
    # (bossJumping flips each jump), and attacks the base once it gets there.
    boss_level: bool = False
    boss_path: list[int] = field(default_factory=list)   # Location addresses; the last is the base
    boss_index: int = 0
    boss_jumping: bool = False        # it moves on our next jump
    arrived_at_base: int = 0
    boss_stage: int = 0

    @property
    def boss_beacon(self) -> int | None:
        return self.boss_path[self.boss_index] if self.boss_level and 0 <= self.boss_index < len(self.boss_path) else None

    def boss_after_jumps(self, jumps: int) -> int | None:
        """Where the Flagship will be after this many more jumps of ours."""
        if self.boss_beacon is None:
            return None
        moves = (jumps + (1 if self.boss_jumping else 0)) // 2
        return self.boss_path[min(len(self.boss_path) - 1, self.boss_index + moves)]

    def jumps_until_base(self) -> int | None:
        """Jumps of ours before the Flagship reaches the base."""
        if self.boss_beacon is None:
            return None
        left = len(self.boss_path) - 1 - self.boss_index
        return max(0, 2 * left - (1 if self.boss_jumping else 0))

    def fleet_move(self, from_nebula: bool) -> int:
        """Fleet advance for the next jump, as StarMap::GetNextDangerMove computes it."""
        if self.fleet[0] > 59 or self.pursuit_delay < 0:
            return 0
        normal = 128 if self.pursuit_delay >= 1 else 64
        if from_nebula:
            return normal // 2 if not self.nebula_map else (102 if self.pursuit_delay >= 1 else 51)
        return normal

    def screen_pos(self, beacon: Beacon) -> tuple[float, float]:
        return self.origin[0] + beacon.x, self.origin[1] + beacon.y

    def fleet_margin(self, beacon: Beacon) -> float:
        """Map distance from the fleet's edge to the beacon (negative: overtaken)."""
        return math.hypot(beacon.x - self.fleet[0], beacon.y - self.fleet[1]) - self.fleet_radius

    def jumps_to_exit(self) -> dict[int, int]:
        """Breadth-first jump counts to the exit through beacons the fleet has not taken."""
        exits = [b.address for b in self.beacons.values() if b.exit]
        distance = {a: 0 for a in exits}
        frontier = list(exits)
        while frontier:
            nxt = []
            for address in frontier:
                for other in self.beacons[address].connected:
                    if other in self.beacons and other not in distance and not self.beacons[other].overtaken:
                        distance[other] = distance[address] + 1
                        nxt.append(other)
            frontier = nxt
        return distance


def _has_store(proc: FTLProcess, event: int) -> bool:
    """The game creates the Store object (pStore) on arrival, so before that look for an
    event with the store flag in the first two levels of the tree."""
    if proc.ptr(event + L.LocationEvent.P_STORE) or proc.bool(event + L.LocationEvent.STORE):
        return True
    begin, count = proc.vector_range(event + L.LocationEvent.CHOICES, L.EventChoice.SIZE)
    for index in range(count):
        child = proc.ptr(begin + index * L.EventChoice.SIZE + L.EventChoice.EVENT)
        if child and proc.bool(child + L.LocationEvent.STORE):
            return True
    return False


def read_star_map(proc: FTLProcess) -> StarMapState:
    world = proc.ptr(proc.ptr(L.P_THE_APP) + L.CApp.WORLD)
    base = world + L.WorldManager.STAR_MAP
    view = proc.view(base, M.SIZE)
    position = view.pair_i(M.POSITION)
    translation = view.pair_f(M.TRANSLATION)

    beacons = {}
    for address in proc.pointer_vector(base + M.LOCATIONS):
        loc = proc.view(address, L.Location.SIZE)
        x, y = loc.pair_f(L.Location.LOC)
        event = loc.ptr(L.Location.EVENT)
        beacons[address] = Beacon(
            address=address, x=x, y=y,
            connected=proc.pointer_vector(address + L.Location.CONNECTED),
            known=loc.bool(L.Location.KNOWN),
            visited=loc.i32(L.Location.VISITED) > 0,
            exit=loc.bool(L.Location.BEACON),
            overtaken=loc.bool(L.Location.DANGER_ZONE),
            nebula=loc.bool(L.Location.NEBULA),
            store=bool(event) and _has_store(proc, event),
            event=event,
            event_name=proc.string(event + L.LocationEvent.EVENT_NAME) if event else "",
        )

    def button(offset: int):
        if not view.bool(offset + L.GenericButton.ACTIVE):
            return None
        bx, by, bw, bh = struct.unpack_from("<iiii", view.data, offset + L.GenericButton.HITBOX)
        return (position[0] + bx, position[1] + by, bw, bh)

    end_button = button(M.END_BUTTON)

    state = StarMapState(
        open=view.bool(L.FocusWindow.OPEN),
        choosing_sector=view.bool(M.CHOOSING_SECTOR),
        origin=(position[0] + translation[0], position[1] + translation[1]),
        current=view.ptr(M.CURRENT_LOC),
        hover=view.ptr(M.HOVER_LOC),
        potential=view.ptr(M.POTENTIAL_LOC),
        beacons=beacons,
        fleet=view.pair_i(M.FLEET_CENTER),
        fleet_radius=view.f32(M.FLEET_RADIUS),
        sector=view.i32(M.WORLD_LEVEL) + 1,
        end_button=end_button,
        pursuit_delay=view.i32(M.PURSUIT_DELAY),
        nebula_map=view.bool(M.NEBULA_MAP),
        wait_button=button(M.WAIT_BUTTON),
        distress_button=button(M.DISTRESS_BUTTON),
        distress_on=view.bool(M.DISTRESS_ON),
        out_of_fuel=view.bool(M.OUT_OF_FUEL),
    )

    if view.bool(M.BOSS_LEVEL):
        state.boss_level = True
        state.boss_path = proc.pointer_vector(base + M.BOSS_PATH)
        state.boss_index = view.i32(M.BOSS_LOC)
        state.boss_jumping = view.bool(M.BOSS_JUMPING)
        state.arrived_at_base = view.i32(M.ARRIVED_AT_BASE)
        state.boss_stage = view.i32(M.BOSS_STAGE)

    if state.choosing_sector:
        offset = view.pair_i(M.SECTOR_MAP_OFFSET)
        boxes_begin, box_count = proc.vector_range(base + M.SECTOR_HIT_BOXES, 16)
        boxes = []
        if box_count:
            raw = proc.read(boxes_begin, box_count * 16)
            boxes = [struct.unpack_from("<iiii", raw, i * 16) for i in range(box_count)]
        current_sector = view.ptr(M.CURRENT_SECTOR)
        neighbors = proc.pointer_vector(current_sector + L.Sector.NEIGHBORS) if current_sector else []
        for index, (sector, box) in enumerate(zip(neighbors, boxes)):
            state.sector_options.append(SectorOption(
                index=index,
                kind=proc.string(sector + L.Sector.KIND),
                name=proc.string(sector + L.Sector.NAME),
                rect=(offset[0] + box[0], offset[1] + box[1], box[2], box[3]),
            ))
    return state
