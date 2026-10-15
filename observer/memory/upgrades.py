"""Read the ship upgrade screen: one box per system, plus the reactor button."""

import struct
from dataclasses import dataclass, field

from observer.memory import layout as L
from observer.memory.process import FTLProcess


@dataclass
class UpgradeOption:
    address: int
    system: str                       # "shields", "engines", ...
    type_id: int
    level: int                        # current level (including pending upgrades)
    max_level: int
    next_cost: int | None             # None when maxed out
    rect: tuple[int, int, int, int]
    hover: bool
    pending: int


@dataclass
class UpgradesState:
    open: bool                        # ship screen open on the Upgrades tab
    address: int
    position: tuple[int, int]
    options: list[UpgradeOption] = field(default_factory=list)
    reactor_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    reactor_hover: bool = False
    reactor_pending: int = 0

    def find(self, address: int) -> UpgradeOption | None:
        return next((o for o in self.options if o.address == address), None)


def upgrades_window(proc: FTLProcess) -> int:
    gui = proc.ptr(proc.ptr(L.P_THE_APP) + L.CApp.GUI)
    windows = proc.pointer_vector(gui + L.CommandGui.SHIP_SCREENS + L.TabbedWindow.WINDOWS)
    return windows[0] if windows else 0


def read_upgrades(proc: FTLProcess) -> UpgradesState | None:
    gui = proc.ptr(proc.ptr(L.P_THE_APP) + L.CApp.GUI)
    screens = gui + L.CommandGui.SHIP_SCREENS
    window = upgrades_window(proc)
    if window == 0:
        return None
    is_open = proc.bool(screens + L.FocusWindow.OPEN) and proc.i32(screens + L.TabbedWindow.CURRENT_TAB) == 0
    state = UpgradesState(open=is_open, address=window, position=proc.view(window, 0x20).pair_i(0x18))

    for box in proc.pointer_vector(window + L.Upgrades.BOXES):
        view = proc.view(box, L.UpgradeBox.SIZE)
        system = view.ptr(L.UpgradeBox.SYSTEM)
        if system == 0 or view.bool(L.UpgradeBox.DUMMY):
            continue
        sys_view = proc.view(system, L.ShipSystem.SIZE)
        type_id = sys_view.i32(L.ShipSystem.TYPE)
        pending = view.i32(L.UpgradeBox.TEMP)
        level = sys_view.pair_i(L.ShipSystem.POWER)[1] + pending
        max_level = sys_view.i32(L.ShipSystem.MAX_LEVEL)
        blueprint = view.ptr(L.UpgradeBox.BLUEPRINT)
        begin, count = proc.vector_range(blueprint + L.SYSTEM_BLUEPRINT_UPGRADE_COSTS, 4) if blueprint else (0, 0)
        cost_list = list(struct.unpack(f"<{count}i", proc.read(begin, count * 4))) if count else []
        # upgradeCosts[i] is the price of reaching level i + 2 (level 1 is the base system).
        index = level - 1
        next_cost = cost_list[index] if level < max_level and 0 <= index < len(cost_list) else None
        button = view.ptr(L.UpgradeBox.CURRENT_BUTTON)
        button_view = proc.view(button, 0x50) if button else None
        state.options.append(UpgradeOption(
            address=box,
            system=L.SYSTEM_NAMES.get(type_id, f"system_{type_id}"),
            type_id=type_id,
            level=level,
            max_level=max_level,
            next_cost=next_cost,
            rect=struct.unpack_from("<iiii", button_view.data, L.GenericButton.HITBOX) if button_view else (0, 0, 0, 0),
            hover=button_view.bool(L.GenericButton.HOVER) if button_view else False,
            pending=pending,
        ))

    reactor = proc.view(window + L.Upgrades.REACTOR_BUTTON, 0x90)
    state.reactor_rect = struct.unpack_from("<iiii", reactor.data, L.GenericButton.HITBOX)
    state.reactor_hover = reactor.bool(L.GenericButton.HOVER)
    state.reactor_pending = proc.i32(window + L.Upgrades.REACTOR_TEMP)
    return state
