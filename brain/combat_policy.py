from controller.actions import Action


def choose_target_priority(state: dict, context: dict) -> str:
    hull = state["hull"]
    missiles = state["missiles"]

    enemy_blueprints = context.get("enemy_blueprints", [])
    enemy_text = " ".join(enemy_blueprints).upper()

    if hull <= 10:
        return "enemy_weapons"

    if "AUTO" in enemy_text:
        return "enemy_weapons"

    if missiles > 0:
        return "enemy_shields"

    return "enemy_weapons"


def should_use_missiles(state: dict, context: dict) -> bool:
    missiles = state["missiles"]
    hull = state["hull"]

    if missiles <= 2:
        return False

    if hull <= 12:
        return True

    enemy_blueprints = context.get("enemy_blueprints", [])
    enemy_text = " ".join(enemy_blueprints).upper()

    if "SCOUT" in enemy_text:
        return True

    return missiles >= 5


def decide_combat(state: dict, context: dict) -> Action:
    hull = state["hull"]
    missiles = state["missiles"]
    fuel = state["fuel"]

    weapons_detected = context.get("weapons_detected", [])
    enemy_blueprints = context.get("enemy_blueprints", [])
    combat_confidence = context.get("combat_confidence", 0)

    if combat_confidence < 60:
        return Action(
            name="combat_uncertain_hold",
            priority=60,
            reason="Combat evidence is weak. Do not commit to combat actions yet.",
            data={
                "combat_confidence": combat_confidence,
                "enemy_blueprints": enemy_blueprints,
            },
        )

    if hull <= 6:
        return Action(
            name="combat_escape_or_full_defense",
            priority=100,
            reason="Hull is critically low. Priority is survival: pause, disable enemy weapons, and escape if possible.",
            data={
                "hull": hull,
                "target": "enemy_weapons",
                "mode": "defensive",
                "use_missiles": missiles > 0,
                "enemy_blueprints": enemy_blueprints,
            },
        )

    if hull <= 12:
        return Action(
            name="disable_enemy_weapons",
            priority=95,
            reason="Hull is low. Reducing incoming damage by targeting enemy weapons is recommended.",
            data={
                "hull": hull,
                "target": "enemy_weapons",
                "use_missiles": missiles > 0,
                "enemy_blueprints": enemy_blueprints,
            },
        )

    if missiles <= 2:
        return Action(
            name="conserve_missiles_and_use_lasers",
            priority=75,
            reason="Missiles are low. Conserve missiles and rely on lasers unless danger increases.",
            data={
                "missiles": missiles,
                "target": "enemy_shields",
                "use_missiles": False,
                "known_weapons": weapons_detected,
                "enemy_blueprints": enemy_blueprints,
            },
        )

    target = choose_target_priority(state, context)
    use_missiles = should_use_missiles(state, context)

    return Action(
        name="standard_combat_attack",
        priority=80,
        reason="Combat is active and the ship is stable. Attack the most valuable enemy system.",
        data={
            "target": target,
            "use_missiles": use_missiles,
            "fuel": fuel,
            "missiles": missiles,
            "known_weapons": weapons_detected,
            "enemy_blueprints": enemy_blueprints,
            "combat_confidence": combat_confidence,
        },
    )