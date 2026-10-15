"""Read the ship's equipment: weapon and drone slots, the cargo hold, augments, and the
over-capacity box an item lands in when nothing has room for it.

The boxes live in CommandGui.equipScreen (an Equipment window). The same object is the
ship screen's Equipment tab and the store's Sell tab, and its boxes (with their hitboxes in
game coordinates) exist while it is closed too, so the cargo can be read at any time.
Dragging an item onto another box swaps the two; dropping it on the store's sell box sells it.
"""

import struct
from dataclasses import dataclass, field

from observer.memory import layout as L
from observer.memory.blueprints import drone_stats, weapon_stats
from observer.memory.process import FTLProcess


@dataclass
class EquipBox:
    address: int
    kind: str                         # weapon, drone, augment, cargo, crew, overflow, overflow_aug
    slot: int
    rect: tuple[int, int, int, int]   # hitbox, game coordinates
    item_kind: str = ""               # weapon, drone, augment, crew, or "" when empty
    item: str = ""                    # blueprint name
    stats: dict = field(default_factory=dict)
    hover: bool = False
    blocked: bool = False
    locked: bool = False

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.rect
        return x + w / 2, y + h / 2


@dataclass
class EquipmentState:
    open: bool
    over_capacity: bool
    over_aug_capacity: bool
    store_mode: bool
    dragging: bool
    sell_box: tuple[int, int]         # DropBox position (store mode)
    boxes: list[EquipBox] = field(default_factory=list)

    def of(self, kind: str) -> list[EquipBox]:
        return sorted((b for b in self.boxes if b.kind == kind), key=lambda b: b.slot)

    @property
    def overflow(self) -> EquipBox | None:
        return next((b for b in self.boxes if b.kind == "overflow"), None)

    def find(self, address: int) -> EquipBox | None:
        return next((b for b in self.boxes if b.address == address), None)


def _read_box(proc: FTLProcess, address: int, kind: str) -> EquipBox:
    view = proc.view(address, L.EquipmentBox.SIZE)
    weapon, drone, crew, augment = struct.unpack_from("<QQQQ", view.data, L.EquipmentBox.ITEM)
    box = EquipBox(address=address, kind=kind, slot=view.i32(L.EquipmentBox.SLOT),
                   rect=struct.unpack_from("<iiii", view.data, L.EquipmentBox.HITBOX),
                   hover=view.bool(L.EquipmentBox.HOVER), blocked=view.bool(L.EquipmentBox.BLOCKED),
                   locked=view.bool(L.EquipmentBox.LOCKED))
    if weapon:
        box.item_kind, box.stats = "weapon", weapon_stats(proc, proc.ptr(weapon + L.ProjectileFactory.BLUEPRINT))
    elif drone:
        box.item_kind, box.stats = "drone", drone_stats(proc, proc.ptr(drone + L.Drone.BLUEPRINT))
    elif augment:
        box.item_kind, box.stats = "augment", {"name": proc.string(augment + L.Blueprint.NAME),
                                               "cost": proc.i32(augment + L.BLUEPRINT_COST)}
    elif crew:
        box.item_kind, box.stats = "crew", {"name": "crew"}
    box.item = box.stats.get("name", "")
    return box


def read_equipment(proc: FTLProcess) -> EquipmentState:
    gui = proc.ptr(proc.ptr(L.P_THE_APP) + L.CApp.GUI)
    base = gui + L.CommandGui.EQUIP_SCREEN
    view = proc.view(base, 0x338)
    overflow, over_aug = view.ptr(L.Equipment.OVERCAPACITY_BOX), view.ptr(L.Equipment.OVER_AUG_BOX)
    state = EquipmentState(open=view.bool(L.FocusWindow.OPEN), over_capacity=view.bool(L.Equipment.OVER_CAPACITY),
                           over_aug_capacity=view.bool(L.Equipment.OVER_AUG_CAPACITY),
                           store_mode=view.bool(L.Equipment.STORE_MODE), dragging=view.bool(L.Equipment.DRAGGING),
                           sell_box=view.pair_i(L.Equipment.SELL_BOX))
    for address in proc.pointer_vector(base + L.Equipment.BOXES):
        if address == overflow:
            kind = "overflow"
        elif address == over_aug:
            kind = "overflow_aug"
        else:
            kind = L.EQUIP_BOX_VTABLES.get(proc.ptr(address), "unknown")
        state.boxes.append(_read_box(proc, address, kind))
    return state
