from pathlib import Path

from config import FTL_CONTINUE_SAVE
from observer.basic_state_reader import read_basic_state_values
from observer.context_reader import read_context
from observer.system_state_reader import read_system_state


def build_game_state(
    path: Path = FTL_CONTINUE_SAVE,
    previous_values: dict | None = None,
) -> dict:
    basic_state = read_basic_state_values(path, previous_values)
    context = read_context(path)
    system_state = read_system_state(path)

    context["systems"] = system_state.get("systems", {})
    context["system_profiles"] = system_state.get("_matched_profiles", [])
    context["system_file_size"] = system_state.get("_file_size")

    return {
        "basic": basic_state,
        "context": context,
        "systems": system_state,
    }


def print_game_state(game_state: dict):
    basic = game_state["basic"]
    context = game_state["context"]
    systems = game_state.get("systems", {})

    print("[game_state] Basic state:")
    print(f"  Hull:     {basic['hull']}")
    print(f"  Fuel:     {basic['fuel']}")
    print(f"  Drones:   {basic['drones']}")
    print(f"  Missiles: {basic['missiles']}")
    print(f"  Scrap:    {basic['scrap']}")

    print()
    print("[game_state] Context:")
    print(f"  Screen:             {context['screen']}")
    print(f"  Enemy present:      {context['enemy_present']}")
    print(f"  Combat confidence:  {context.get('combat_confidence', 0)}")
    print(f"  Store present:      {context['store_present']}")
    print(f"  Event present:      {context['event_present']}")

    enemy_blueprints = context.get("enemy_blueprints", [])

    if enemy_blueprints:
        print()
        print("[game_state] Enemy blueprints:")
        for enemy in enemy_blueprints:
            print(f"  - {enemy}")

    weapon_strings = context.get("weapons_detected", [])

    if weapon_strings:
        print()
        print("[game_state] Weapon strings found, not necessarily owned:")
        for weapon in weapon_strings:
            print(f"  - {weapon}")

    print()
    print("[game_state] System state:")

    matched_profiles = systems.get("_matched_profiles", [])
    system_values = systems.get("systems", {})

    if not matched_profiles:
        print("  No matching system layout profile.")
        return

    print("  Matched profiles:")
    for profile_name in matched_profiles:
        print(f"    - {profile_name}")

    print("  Known systems:")
    for system_name, info in system_values.items():
        print(
            f"    {system_name}: {info['value']} "
            f"@ 0x{info['offset']:08X}"
        )


def main():
    if not FTL_CONTINUE_SAVE.exists():
        print("[game_state] continue.sav does not exist.")
        return

    game_state = build_game_state()
    print_game_state(game_state)


if __name__ == "__main__":
    main()