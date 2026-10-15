"""Read the store at the current beacon: what is for sale, prices, stock and button positions."""

import struct
from dataclasses import dataclass, field

from observer.memory import layout as L
from observer.memory.blueprints import drone_stats, weapon_stats
from observer.memory.process import FTLProcess


@dataclass
class StoreItem:
    address: int
    kind: str                         # item, repair, weapon, drone, augment, system, crew, unknown
    name: str                         # blueprint name, e.g. "fuel", "BURST_LASER_1"
    cost: int
    count: int                        # stock left
    rect: tuple[int, int, int, int]   # button hitbox (x, y, w, h), game coords
    active: bool
    hover: bool
    repair_all: bool = False
    page: int = 0                     # 0: always shown (fuel, missiles, drone parts, repairs), else 1 or 2
    stats: dict = field(default_factory=dict)   # weapons and drones: what the blueprint says


@dataclass
class StoreState:
    address: int
    position: tuple[int, int] = (0, 0)   # Store window position (FocusWindow.position)
    items: list[StoreItem] = field(default_factory=list)
    page: int = 1                         # page shown now
    page_buttons: dict = field(default_factory=dict)   # page -> (hitbox, hover)
    confirm_open: bool = False            # a purchase waits on the Yes/No dialog
    confirm_yes: tuple = ((0, 0, 0, 0), False)          # (hitbox, hover) of Yes
    confirm_position: tuple[int, int] = (0, 0)          # the dialog's FocusWindow position
    weapon_slots: int = 0
    weapons_held: int = 0
    drone_slots: int = 0
    drones_held: int = 0

    def find(self, address: int) -> StoreItem | None:
        return next((item for item in self.items if item.address == address), None)


def current_store_address(proc: FTLProcess) -> int:
    world = proc.ptr(proc.ptr(L.P_THE_APP) + L.CApp.WORLD)
    location = proc.ptr(world + L.WorldManager.STAR_MAP + L.StarMap.CURRENT_LOC)
    event = proc.ptr(location + L.Location.EVENT) if location else 0
    return proc.ptr(event + L.LocationEvent.P_STORE) if event else 0


def _blueprint_stats(proc: FTLProcess, kind: str, blueprint: int) -> dict:
    if kind == "weapon":
        return weapon_stats(proc, blueprint)
    if kind == "drone":
        return drone_stats(proc, blueprint)
    return {}


def _button(view, offset: int) -> tuple[tuple[int, int, int, int], bool]:
    return struct.unpack_from("<iiii", view.data, offset + L.GenericButton.HITBOX), view.bool(offset + L.GenericButton.HOVER)


def _read_box(proc: FTLProcess, address: int) -> StoreItem:
    view = proc.view(address, L.StoreBox.SIZE)
    kind = L.STORE_BOX_VTABLES.get(view.ptr(0), "unknown")
    blueprint = view.ptr(L.StoreBox.BLUEPRINT)
    item_name = ""
    if kind == "item":
        # Fuel, missiles and drone parts: FTL's ItemBlueprint has an empty name, so they were
        # never recognised (or bought). The box's button image names the item instead:
        # "storeUI/store_items_fuel" / "_missiles" / "_drones".
        image = proc.string(address + L.StoreBox.BUTTON_IMAGE)
        item_name = image.rsplit("_", 1)[-1] if image.startswith("storeUI/store_items_") else ""
    button = L.StoreBox.BUTTON
    rect = struct.unpack_from("<iiii", view.data, button + L.GenericButton.HITBOX)
    cost = view.i32(L.StoreBox.COST)
    repair_all = False
    if kind == "repair":
        repair_all = view.bool(L.StoreBox.REPAIR_ALL)
        cost = view.i32(L.StoreBox.REPAIR_COST)
    return StoreItem(
        address=address,
        kind=kind,
        name=item_name or (proc.string(blueprint + L.Blueprint.NAME) if blueprint else kind),
        cost=cost,
        count=view.i32(L.StoreBox.COUNT),
        rect=rect,
        active=view.bool(button + L.GenericButton.ACTIVE),
        hover=view.bool(button + L.GenericButton.HOVER),
        repair_all=repair_all,
        stats=_blueprint_stats(proc, kind, blueprint),
    )


def read_store(proc: FTLProcess) -> StoreState | None:
    store = current_store_address(proc)
    if store == 0:
        return None
    view = proc.view(store, L.Store.SIZE)
    state = StoreState(address=store, position=view.pair_i(0x18))
    for address in proc.pointer_vector(store + L.Store.ITEM_BOXES):
        state.items.append(_read_box(proc, address))
    # Store::MouseMove: BOXES[0..5] show on page 1 and BOXES[6 .. 3*sectionCount-1] on page 2;
    # boxes after the sections (the hull repair buttons) are always shown.
    sectioned = 3 * view.i32(L.Store.SECTION_COUNT)
    for index, address in enumerate(proc.pointer_vector(store + L.Store.BOXES)):
        item = _read_box(proc, address)
        if item.kind != "repair" and index < sectioned:
            item.page = 1 if index < L.Store.PAGE1_BOXES else 2
        state.items.append(item)
    state.page = 2 if view.bool(L.Store.PAGE2) else 1
    state.page_buttons = {1: _button(view, L.Store.PAGE1_BUTTON), 2: _button(view, L.Store.PAGE2_BUTTON)}
    state.confirm_open = view.ptr(L.Store.CONFIRM_BUY) != 0
    state.confirm_yes = _button(view, L.Store.CONFIRM_YES)
    state.confirm_position = view.pair_i(L.Store.CONFIRM_DIALOG + 0x18)

    shopper = view.ptr(L.Store.SHOPPER)
    if shopper:
        weapons = proc.ptr(shopper + L.ShipSlots.WEAPON_SYSTEM)
        if weapons:
            state.weapon_slots = proc.i32(weapons + L.ShipSlots.WEAPON_SLOTS)
            state.weapons_held = len(proc.pointer_vector(weapons + L.WeaponSystem.WEAPONS))
        drones = proc.ptr(shopper + L.ShipSlots.DRONE_SYSTEM)
        if drones:
            state.drone_slots = proc.i32(drones + L.ShipSlots.DRONE_SLOTS)
            state.drones_held = len(proc.pointer_vector(drones + L.ShipSlots.DRONES))
    return state
