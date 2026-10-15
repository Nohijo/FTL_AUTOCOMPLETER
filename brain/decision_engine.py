from config import FTL_CONTINUE_SAVE
from controller.actions import Action
from observer.game_state_builder import build_game_state
from brain.combat_policy import decide_combat
from brain.upgrade_policy import decide_upgrade_strategy


# Temporary manual override.
# The player reported having ZERO weapons:
# no installed weapons and no storage weapons.
# Later this must be replaced by observer/weapon_state_reader.py.
SHIP_HAS_NO_WEAPONS_MANUAL_OVERRIDE = False


def decide_survival(state: dict) -> Action | None:
    hull = state["hull"]
    scrap = state["scrap"]

    if hull <= 8:
        if scrap >= 20:
            return Action(
                name="repair_hull_urgent",
                priority=100,
                reason="Hull is critically low. Survival is the top priority.",
                data={"hull": hull, "scrap": scrap},
            )

        return Action(
            name="avoid_risk",
            priority=95,
            reason="Hull is critically low and there is not enough scrap to repair.",
            data={"hull": hull, "scrap": scrap},
        )

    if hull <= 15 and scrap >= 15:
        return Action(
            name="repair_hull",
            priority=85,
            reason="Hull is low. Repairing before taking more risks is recommended.",
            data={"hull": hull, "scrap": scrap},
        )

    return None


def decide_no_weapon_policy(state: dict, context: dict) -> Action | None:
    if not SHIP_HAS_NO_WEAPONS_MANUAL_OVERRIDE:
        return None

    hull = state["hull"]
    fuel = state["fuel"]
    scrap = state["scrap"]

    store_present = context.get("store_present", False)
    enemy_present = context.get("enemy_present", False)
    combat_confidence = context.get("combat_confidence", 0)
    weapon_strings = context.get("weapons_detected", [])

    systems = context.get("systems", {})
    weapons_info = systems.get("weapons_level")
    weapons_level = None

    if weapons_info is not None:
        weapons_level = weapons_info.get("value")

    if enemy_present and combat_confidence >= 60:
        return Action(
            name="combat_escape_no_weapons",
            priority=110,
            reason=(
                "The ship has no usable weapons and appears to be in combat. "
                "Do not try to fight. Prioritize survival and escape as soon as possible."
            ),
            data={
                "hull": hull,
                "fuel": fuel,
                "scrap": scrap,
                "weapons_level": weapons_level,
                "risk": "critical_no_weapons_in_combat",
                "goal": "escape_combat",
            },
        )

    if store_present and scrap >= 50:
        return Action(
            name="store_buy_weapon_urgent",
            priority=100,
            reason=(
                "The ship has no usable weapons and a store is available. "
                "Buying any viable weapon is the top priority."
            ),
            data={
                "scrap": scrap,
                "fuel": fuel,
                "hull": hull,
                "weapons_level": weapons_level,
                "risk": "critical_no_weapons",
                "minimum_goal": "buy_any_viable_weapon",
                "weapon_strings_not_necessarily_owned": weapon_strings,
            },
        )

    if store_present and scrap < 50:
        return Action(
            name="leave_store_no_weapon_money",
            priority=98,
            reason=(
                "The ship has no usable weapons, but scrap is too low to buy one. "
                "Leave the store, seek scrap, avoid unnecessary fights, and prioritize acquiring any viable weapon."
            ),
            data={
                "scrap": scrap,
                "fuel": fuel,
                "hull": hull,
                "weapons_level": weapons_level,
                "risk": "critical_no_weapons",
                "weapon_strings_not_necessarily_owned": weapon_strings,
            },
        )

    return Action(
        name="seek_weapon_or_scrap_critical",
        priority=97,
        reason=(
            "The ship has no usable weapons. The bot should seek scrap, weapon rewards, or a store, "
            "and avoid combat whenever possible."
        ),
        data={
            "scrap": scrap,
            "fuel": fuel,
            "hull": hull,
            "weapons_level": weapons_level,
            "risk": "critical_no_weapons",
            "goal": "get_any_viable_weapon",
        },
    )


def decide_fuel_policy(state: dict) -> Action | None:
    fuel = state["fuel"]
    scrap = state["scrap"]

    if fuel <= 3:
        if scrap >= 3:
            return Action(
                name="buy_fuel",
                priority=90,
                reason="Fuel is critically low. Without fuel the run can get stuck.",
                data={"fuel": fuel, "scrap": scrap},
            )

        return Action(
            name="seek_fuel_event",
            priority=88,
            reason="Fuel is critically low and there is not enough scrap to buy more.",
            data={"fuel": fuel, "scrap": scrap},
        )

    if fuel <= 6 and scrap >= 6:
        return Action(
            name="buy_some_fuel",
            priority=70,
            reason="Fuel is somewhat low. Buying some fuel is recommended if a store is available.",
            data={"fuel": fuel, "scrap": scrap},
        )

    return None


def decide_ammo_policy(state: dict) -> Action | None:
    drones = state["drones"]
    missiles = state["missiles"]
    scrap = state["scrap"]

    if missiles <= 2 and scrap >= 6:
        return Action(
            name="buy_missile",
            priority=55,
            reason="Missiles are low. Buying missiles may be useful only if the ship uses missile weapons.",
            data={"missiles": missiles, "scrap": scrap},
        )

    if drones <= 1 and scrap >= 8:
        return Action(
            name="buy_drone_part",
            priority=50,
            reason="Drone parts are low. Buying drone parts may be useful only if the ship uses drones.",
            data={"drones": drones, "scrap": scrap},
        )

    return None


def decide_upgrade_policy(state: dict) -> Action | None:
    scrap = state["scrap"]

    if scrap >= 100:
        return Action(
            name="look_for_major_upgrade",
            priority=65,
            reason="There is a lot of scrap available. Look for a major weapon, system, or strong upgrade.",
            data={"scrap": scrap},
        )

    if scrap >= 70:
        return Action(
            name="consider_system_upgrade",
            priority=55,
            reason="There is enough scrap to consider upgrading shields, engines, weapons, or reactor.",
            data={"scrap": scrap},
        )

    if scrap >= 40:
        return Action(
            name="save_or_upgrade",
            priority=45,
            reason="There is moderate scrap. Saving or making a small system upgrade is reasonable.",
            data={"scrap": scrap},
        )

    return None


def decide_store_policy(state: dict, context: dict) -> Action | None:
    if not context.get("store_present", False):
        return None

    hull = state["hull"]
    fuel = state["fuel"]
    missiles = state["missiles"]
    drones = state["drones"]
    scrap = state["scrap"]

    systems = context.get("systems", {})
    weapons_info = systems.get("weapons_level")
    weapons_level = None

    if weapons_info is not None:
        weapons_level = weapons_info.get("value")

    weapon_strings = context.get("weapons_detected", [])

    if hull <= 20 and scrap >= 10:
        return Action(
            name="store_repair_hull",
            priority=90,
            reason="A store is available and hull is damaged. Repairing is recommended.",
            data={"hull": hull, "scrap": scrap},
        )

    if fuel <= 6 and scrap >= 3:
        return Action(
            name="store_buy_fuel",
            priority=80,
            reason="A store is available and fuel is low. Buying fuel is recommended.",
            data={"fuel": fuel, "scrap": scrap},
        )

    if weapons_level is not None and weapons_level < 5 and scrap >= 50:
        return Action(
            name="store_save_for_weapons_or_weapon_power",
            priority=75,
            reason=(
                "Weapons power is not very high yet. Save scrap for weapon power "
                "or an actual weapon purchase."
            ),
            data={
                "weapons_level": weapons_level,
                "scrap": scrap,
                "weapon_strings_not_necessarily_owned": weapon_strings,
            },
        )

    if missiles <= 3 and scrap >= 6:
        return Action(
            name="store_buy_missiles",
            priority=45,
            reason="A store is available and missiles are low, but this is lower priority than weapons or repairs.",
            data={"missiles": missiles, "scrap": scrap},
        )

    if drones <= 1 and scrap >= 8:
        return Action(
            name="store_buy_drone_parts",
            priority=40,
            reason="Drone parts are low, but buying drone parts is lower priority than weapons, fuel, or repairs.",
            data={"drones": drones, "scrap": scrap},
        )

    if scrap < 10:
        return Action(
            name="leave_store_no_purchase",
            priority=45,
            reason="A store is available, but scrap is too low to buy anything useful. Leave the store and continue exploring.",
            data={
                "hull": hull,
                "fuel": fuel,
                "missiles": missiles,
                "drones": drones,
                "scrap": scrap,
                "weapons_level": weapons_level,
            },
        )

    return Action(
        name="store_save_scrap",
        priority=35,
        reason="A store is available, but there is no urgent purchase based on current resources.",
        data={
            "hull": hull,
            "fuel": fuel,
            "missiles": missiles,
            "drones": drones,
            "scrap": scrap,
            "weapons_level": weapons_level,
            "weapon_strings_not_necessarily_owned": weapon_strings,
        },
    )


def decide_event_policy(state: dict, context: dict) -> Action | None:
    if not context.get("event_present", False):
        return None

    return Action(
        name="evaluate_event_options",
        priority=40,
        reason="An event or text screen seems present. The bot should evaluate event options before choosing.",
        data={
            "event_text_candidates": context.get("event_text_candidates", []),
            "fuel": state["fuel"],
            "hull": state["hull"],
            "scrap": state["scrap"],
        },
    )


def decide_exploration_policy(state: dict) -> Action:
    return Action(
        name="continue_exploring",
        priority=30,
        reason="The ship is stable. Continue exploring and gathering resources.",
        data={
            "hull": state["hull"],
            "fuel": state["fuel"],
            "drones": state["drones"],
            "missiles": state["missiles"],
            "scrap": state["scrap"],
        },
    )


def decide_economy_or_survival(state: dict, context: dict) -> Action:
    candidates = [
        decide_survival(state),
        decide_no_weapon_policy(state, context),
        decide_store_policy(state, context),
        decide_fuel_policy(state),
        decide_ammo_policy(state),
        decide_upgrade_strategy(state, context),
        decide_upgrade_policy(state),
        decide_event_policy(state, context),
        decide_exploration_policy(state),
    ]

    valid_candidates = [
        action for action in candidates
        if action is not None
    ]

    valid_candidates.sort(key=lambda action: action.priority, reverse=True)

    return valid_candidates[0]


def decide_next_action(game_state: dict) -> Action:
    state = game_state["basic"]
    context = game_state["context"]

    # Critical no-weapon state overrides normal combat/economy logic.
    no_weapon_action = decide_no_weapon_policy(state, context)

    if no_weapon_action is not None:
        return no_weapon_action

    enemy_present = context.get("enemy_present", False)
    screen = context.get("screen", "unknown")
    combat_confidence = context.get("combat_confidence", 0)

    if (
        enemy_present
        and screen == "combat_or_hostile_event"
        and combat_confidence >= 60
    ):
        return decide_combat(state, context)

    return decide_economy_or_survival(state, context)


def print_decision(game_state: dict, action: Action):
    state = game_state["basic"]
    context = game_state["context"]

    print()
    print("[brain] Current state:")
    print(f"  Hull:     {state['hull']}")
    print(f"  Fuel:     {state['fuel']}")
    print(f"  Drones:   {state['drones']}")
    print(f"  Missiles: {state['missiles']}")
    print(f"  Scrap:    {state['scrap']}")

    print()
    print("[brain] Inferred context:")
    print(f"  Screen:             {context.get('screen', 'unknown')}")
    print(f"  Enemy present:      {context.get('enemy_present', False)}")
    print(f"  Combat confidence:  {context.get('combat_confidence', 0)}")
    print(f"  Store present:      {context.get('store_present', False)}")
    print(f"  Event present:      {context.get('event_present', False)}")

    systems = context.get("systems", {})

    if systems:
        print("  Known systems:")
        for system_name, info in systems.items():
            print(f"    - {system_name}: {info.get('value')}")

    enemy_blueprints = context.get("enemy_blueprints", [])

    if enemy_blueprints:
        print("  Enemy blueprints:")
        for enemy in enemy_blueprints:
            print(f"    - {enemy}")

    weapon_strings = context.get("weapons_detected", [])

    if weapon_strings:
        print("  Weapon strings found, not necessarily owned:")
        for weapon in weapon_strings:
            print(f"    - {weapon}")

    if SHIP_HAS_NO_WEAPONS_MANUAL_OVERRIDE:
        print("  Manual weapon override:")
        print("    - owned_weapons: 0")
        print("    - installed_weapons: 0")
        print("    - storage_weapons: 0")

    event_texts = context.get("event_text_candidates", [])

    if event_texts:
        print()
        print("[brain] Possible event text:")
        for text in event_texts:
            print(f"  - {text}")

    print()
    print("[brain] Suggested decision:")
    print(f"  Action:    {action.name}")
    print(f"  Priority:  {action.priority}")
    print(f"  Reason:    {action.reason}")
    print(f"  Data:      {action.data}")


def main():
    if not FTL_CONTINUE_SAVE.exists():
        print("[brain] continue.sav does not exist. There is no active run.")
        return

    game_state = build_game_state()
    action = decide_next_action(game_state)

    print_decision(game_state, action)


if __name__ == "__main__":
    main()