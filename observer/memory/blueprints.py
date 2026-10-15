"""Read item blueprints (weapons, drones) into plain dicts, the same way wherever the item is:
mounted, in the cargo hold, for sale in a store, or waiting in the over-capacity box."""

from observer.memory import layout as L
from observer.memory.process import FTLProcess

# WeaponBlueprint.Damage (embedded at WeaponBlueprint.DAMAGE)                  DWARF
_DAMAGE_FIELDS = {"damage": 0x00, "pierce": 0x04, "fire": 0x08, "breach": 0x0C, "stun_chance": 0x10,
                  "ion": 0x14, "system_damage": 0x18, "crew_damage": 0x1C}
_HULL_BUSTER = 0x20
_LOCKDOWN = 0x2C
_LENGTH = 0xC4                 # beam length
_MINI_COUNT = 0xCC             # flak: real projectiles per shot
_CHARGE_LEVELS = 0x184         # chain / charge weapons


def weapon_stats(proc: FTLProcess, blueprint: int) -> dict:
    """Everything the bot weighs about a weapon. Fire and breach chances are in tenths."""
    if not blueprint:
        return {}
    view = proc.view(blueprint, L.WeaponBlueprint.SIZE)
    stats = {"name": proc.string(blueprint + L.Blueprint.NAME),
             "type": proc.string(blueprint + L.WeaponBlueprint.TYPE_NAME),
             "shots": view.i32(L.WeaponBlueprint.SHOTS), "missiles": view.i32(L.WeaponBlueprint.MISSILES),
             "cooldown": view.f32(L.WeaponBlueprint.COOLDOWN), "power": view.i32(L.WeaponBlueprint.POWER),
             "length": view.i32(_LENGTH), "charge_levels": view.i32(_CHARGE_LEVELS),
             "mini_count": view.i32(_MINI_COUNT),
             "cost": view.i32(L.BLUEPRINT_COST)}
    for key, offset in _DAMAGE_FIELDS.items():
        stats[key] = view.i32(L.WeaponBlueprint.DAMAGE + offset)
    stats["hull_buster"] = view.bool(L.WeaponBlueprint.DAMAGE + _HULL_BUSTER)
    stats["lockdown"] = view.bool(L.WeaponBlueprint.DAMAGE + _LOCKDOWN)
    return stats


def drone_stats(proc: FTLProcess, blueprint: int) -> dict:
    if not blueprint:
        return {}
    return {"name": proc.string(blueprint + L.Blueprint.NAME),
            "type": proc.string(blueprint + L.DroneBlueprint.TYPE_NAME),
            "power": proc.i32(blueprint + L.DroneBlueprint.POWER),
            "cost": proc.i32(blueprint + L.BLUEPRINT_COST)}
