"""Convert a live memory snapshot into the game_state dict the brain/ policies use.

The brain/ policies were written against the save-file observer
(observer/game_state_builder.py). This produces the same keys from memory, so
those policies work unchanged on live data.
"""

from observer.memory.reader import GameSnapshot


def in_combat(snapshot: GameSnapshot) -> bool:
    """A hostile, living enemy is on screen. Neutral ships (traders, mercenaries) don't count."""
    enemy = snapshot.enemy
    return (
        snapshot.screen == "in_run"
        and enemy is not None
        and snapshot.enemy_origin is not None
        and enemy.targetable_hostile
        and not enemy.destroyed
        and not enemy.surrendered
        and enemy.hull > 0
    )


def to_legacy_game_state(snapshot: GameSnapshot) -> dict:
    player = snapshot.player
    if player is None:
        raise ValueError("no player ship in snapshot")

    combat = in_combat(snapshot)
    if combat:
        screen = "combat_or_hostile_event"
    elif snapshot.store_open:
        screen = "store"
    elif snapshot.event_open:
        screen = "event"
    elif snapshot.map_open:
        screen = "map"
    else:
        screen = "idle"

    basic = {
        "hull": player.hull,
        "fuel": player.fuel,
        "drones": player.drone_parts,
        "missiles": player.missiles,
        "scrap": player.scrap,
    }
    context = {
        "screen": screen,
        "enemy_present": combat,
        "combat_confidence": 100 if combat else 0,
        "store_present": snapshot.store_open or snapshot.location_has_store,
        "event_present": snapshot.event_open,
        "event_text_candidates": [snapshot.event_text] if snapshot.event_text else [],
        "enemy_blueprints": [snapshot.enemy.blueprint] if snapshot.enemy else [],
        "weapons_detected": [weapon.blueprint for weapon in player.weapons],
        "systems": {f"{name}_level": {"value": system.level} for name, system in player.systems.items()},
    }
    return {"basic": basic, "context": context, "systems": {}}
