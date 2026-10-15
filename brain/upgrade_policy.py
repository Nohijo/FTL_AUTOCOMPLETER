from controller.actions import Action


def get_system_value(context: dict, system_name: str) -> int | None:
    systems = context.get("systems", {})
    info = systems.get(system_name)

    if info is None:
        return None

    return info.get("value")


def decide_upgrade_strategy(state: dict, context: dict) -> Action | None:
    hull = state["hull"]
    fuel = state["fuel"]
    missiles = state["missiles"]
    drones = state["drones"]
    scrap = state["scrap"]

    enemy_present = context.get("enemy_present", False)
    store_present = context.get("store_present", False)
    event_present = context.get("event_present", False)

    shields_level = get_system_value(context, "shields_level")
    weapons_level = get_system_value(context, "weapons_level")

    if enemy_present or event_present:
        return None

    if hull <= 15 and scrap >= 20:
        return Action(
            name="seek_repair_before_upgrading",
            priority=90,
            reason="Hull is low. Repairs should be prioritized before upgrades.",
            data={"hull": hull, "scrap": scrap},
        )

    if fuel <= 5:
        return Action(
            name="save_scrap_for_fuel",
            priority=85,
            reason="Fuel is getting low. Save scrap for fuel or a store purchase.",
            data={"fuel": fuel, "scrap": scrap},
        )

    if weapons_level is not None:
        if weapons_level < 5 and scrap >= 50:
            return Action(
                name="upgrade_weapons_level",
                priority=80,
                reason="Weapons level is known and can be improved. More weapon power increases combat capability.",
                data={
                    "weapons_level": weapons_level,
                    "target_weapons_level": weapons_level + 1,
                    "scrap": scrap,
                },
            )

    if shields_level is not None:
        if shields_level < 4 and scrap >= 20:
            return Action(
                name="upgrade_shields_level",
                priority=78,
                reason="Shields level is known and can be improved. Better shields improve survival.",
                data={
                    "shields_level": shields_level,
                    "target_shields_level": shields_level + 1,
                    "scrap": scrap,
                },
            )

    if store_present:
        if scrap >= 50:
            return Action(
                name="store_consider_weapon_or_system",
                priority=70,
                reason="A store is available and there is enough scrap for an important purchase.",
                data={
                    "scrap": scrap,
                    "known_weapons": context.get("weapons_detected", []),
                    "weapons_level": weapons_level,
                    "shields_level": shields_level,
                },
            )

        return None

    if scrap >= 70:
        return Action(
            name="consider_major_upgrade",
            priority=65,
            reason="There is enough scrap for a meaningful upgrade, but exact system data is incomplete.",
            data={
                "scrap": scrap,
                "weapons_level": weapons_level,
                "shields_level": shields_level,
            },
        )

    if scrap >= 40:
        return Action(
            name="save_for_next_major_upgrade",
            priority=50,
            reason="There is moderate scrap. Saving for shields, weapons, or a strong store purchase is reasonable.",
            data={
                "scrap": scrap,
                "weapons_level": weapons_level,
                "shields_level": shields_level,
            },
        )

    if missiles <= 3 or drones <= 2:
        return Action(
            name="continue_exploring_for_resources",
            priority=40,
            reason="Resources are somewhat limited. Continue exploring to gain scrap and supplies.",
            data={
                "missiles": missiles,
                "drones": drones,
                "scrap": scrap,
            },
        )

    return None