from controller.actions import Action


def build_execution_plan(action: Action) -> list[str]:
    if action.name == "seek_weapon_or_scrap_critical":
        return [
            "Do not intentionally enter combat if there is any safer option.",
            "Open the sector map.",
            "Choose a beacon that is most likely to be safe or event-based.",
            "Prioritize events that may give scrap, weapons, fuel, or crew.",
            "Avoid spending scrap on drones, missiles, repairs, or upgrades unless survival requires it.",
            "If a store appears, check whether any affordable weapon can be bought.",
            "Main goal: obtain any viable weapon as soon as possible.",
        ]

    if action.name == "combat_escape_no_weapons":
        return [
            "Pause immediately.",
            "Do not attempt to fight.",
            "Maximize survival until FTL is charged.",
            "If possible, power engines and shields.",
            "Jump away as soon as FTL is ready.",
            "After escaping, seek a store, scrap reward, or weapon reward.",
        ]
    if action.name == "leave_store_no_weapon_money":
        return [
            "Close the store screen.",
            "Return to the beacon/map state.",
            "Avoid optional combat if possible.",
            "Jump to the safest available beacon.",
            "Prioritize events that can give scrap or weapons.",
            "Do not spend scrap on fuel, drones, missiles, or repairs unless survival requires it.",
        ]

    if action.name == "store_buy_weapon_urgent":
        return [
            "Inspect store weapon offers.",
            "Buy the cheapest viable weapon that the ship can power.",
            "Prefer lasers, ions, or beams over missile-only weapons if possible.",
            "Do not buy drone parts, missiles, or repairs before acquiring a weapon.",
        ]

    if action.name == "upgrade_weapons_level":
        return [
            "Open the ship upgrade screen.",
            "Upgrade the weapons system by one level.",
            "Close the upgrade screen.",
            "Save or wait for continue.sav to update.",
        ]

    if action.name == "upgrade_shields_level":
        return [
            "Open the ship upgrade screen.",
            "Upgrade the shields system by one level.",
            "Close the upgrade screen.",
            "Save or wait for continue.sav to update.",
        ]

    if action.name == "continue_exploring":
        return [
            "Open the sector map if not already open.",
            "Choose a safe unvisited beacon.",
            "Prefer non-nebula beacons unless fuel or route requires otherwise.",
            "Jump.",
        ]

    if action.name == "repair_hull_urgent":
        return [
            "Find a store as soon as possible.",
            "Repair hull before buying upgrades.",
            "Avoid optional fights until hull is safe.",
        ]

    if action.name == "buy_fuel":
        return [
            "If in store, buy fuel.",
            "If not in store, prioritize beacons that may give fuel.",
            "Avoid routes that risk running out of fuel.",
        ]

    if action.name == "standard_combat_attack":
        target = action.data.get("target", "enemy_weapons")
        use_missiles = action.data.get("use_missiles", False)

        plan = [
            "Pause the game.",
            f"Target {target}.",
            "Fire available laser/ion/beam weapons when charged.",
        ]

        if use_missiles:
            plan.append("Use missiles only if the target is dangerous or shields must be broken.")
        else:
            plan.append("Conserve missiles.")

        plan.extend([
            "Retarget enemy weapons if hull becomes low.",
            "Jump away if hull becomes critical and FTL is charged.",
        ])

        return plan

    return [
        f"No specific executor plan exists for action: {action.name}",
        "Print the decision and wait for manual handling.",
    ]


def execute_action_dry_run(action: Action):
    print()
    print("[executor] Dry-run execution plan:")
    print(f"  Action: {action.name}")
    print(f"  Priority: {action.priority}")
    print()

    steps = build_execution_plan(action)

    for index, step in enumerate(steps, start=1):
        print(f"  {index}. {step}")