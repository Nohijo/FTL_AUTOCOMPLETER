from pathlib import Path

from config import FTL_CONTINUE_SAVE


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def extract_ascii_strings(data: bytes, min_length: int = 4) -> list[str]:
    strings = []
    current = []

    for byte in data:
        if 32 <= byte <= 126:
            current.append(chr(byte))
        else:
            if len(current) >= min_length:
                strings.append("".join(current))
            current = []

    if len(current) >= min_length:
        strings.append("".join(current))

    return strings


def is_probably_weapon_id(text: str) -> bool:
    upper_text = text.upper()

    ignored_exact = {
        "USED_MISSILE",
        "USED_MISSILES",
        "MISSILE",
        "MISSILES",
        "DRONES",
        "FUEL",
        "SCRAP",
    }

    if upper_text in ignored_exact:
        return False

    ignored_fragments = [
        "USED_",
        "DESTROYED",
        "DEAD_CREW",
        "EVENT_",
    ]

    if any(fragment in upper_text for fragment in ignored_fragments):
        return False

    weapon_keywords = [
        "LASER",
        "MISSILE",
        "MISSILES",
        "BEAM",
        "ION",
        "BOMB",
        "FLAK",
        "BURST",
    ]

    return any(keyword in upper_text for keyword in weapon_keywords)


def detect_player_weapons(strings: list[str]) -> list[str]:
    weapons = []

    for text in strings:
        if len(text) > 50:
            continue

        if is_probably_weapon_id(text):
            if text not in weapons:
                weapons.append(text)

    return weapons


def detect_long_texts(strings: list[str]) -> list[str]:
    return [
        text for text in strings
        if len(text) >= 50
    ][:5]


def is_enemy_ship_blueprint(text: str) -> bool:
    upper_text = text.upper()

    if len(text) > 50:
        return False

    ignored = [
        "PLAYER_SHIP",
        "PLAYER_SHIP_HARD",
        "THE KESTREL",
        "KESTRAL",
        "EVENT_",
        "DESTROYED",
        "DEAD CREW",
    ]

    if any(marker in upper_text for marker in ignored):
        return False

    enemy_family_markers = [
        "PIRATE",
        "REBEL",
        "MANTIS",
        "SLUG",
        "AUTO",
        "ROCK",
        "ENGI",
        "ZOLTAN",
        "CRYSTAL",
        "FED",
        "BOSS",
    ]

    if not any(marker in upper_text for marker in enemy_family_markers):
        return False

    # Common active enemy blueprint forms seen in saves:
    # MANTIS_SCOUT_P, mantis_scout_pirate, etc.
    if upper_text.endswith("_P"):
        return True

    if "_SCOUT_" in upper_text:
        return True

    if "_PIRATE" in upper_text:
        return True

    if "SCOUT" in upper_text and "PIRATE" in upper_text:
        return True

    return False


def detect_enemy_blueprints(strings: list[str]) -> list[str]:
    enemy_blueprints = []

    for text in strings:
        if is_enemy_ship_blueprint(text):
            if text not in enemy_blueprints:
                enemy_blueprints.append(text)

    return enemy_blueprints


def infer_context(strings: list[str]) -> dict:
    joined = "\n".join(strings).upper()

    weapons = detect_player_weapons(strings)
    long_texts = detect_long_texts(strings)
    enemy_blueprints = detect_enemy_blueprints(strings)

    tutorial_markers = [
        "THE DATA YOU CARRY IS VITAL",
        "TIP: GRINDING SECTORS",
        "GET TO THE EXIT BEFORE THE PURSUING REBEL FLEET",
    ]

    store_markers = [
        "STORE_",
        "STORE ",
        "BUY",
        "SELL",
        "REPAIR ALL",
        "FIX HULL",
    ]

    event_markers = [
        "QUEST",
        "DISTRESS",
        "BEACON",
        "TAKE THIS",
        "BUTTON",
        "OPTION",
    ]

    weak_hostile_markers = [
        "PIRATE",
        "REBEL",
        "MANTIS",
        "SLUG",
        "AUTO",
        "SURRENDER",
        "DESTROYED_DEFAULT",
        "DEAD_CREW_DEFAULT",
    ]

    tutorial_present = any(marker in joined for marker in tutorial_markers)
    store_present = any(marker in joined for marker in store_markers)
    event_present = any(marker in joined for marker in event_markers) or bool(long_texts)
    hostile_text_present = any(marker in joined for marker in weak_hostile_markers)

    enemy_present = len(enemy_blueprints) > 0

    if enemy_present:
        combat_confidence = 80
        screen = "combat_or_hostile_event"
    elif tutorial_present:
        combat_confidence = 0
        screen = "tutorial_or_start"
    elif store_present:
        combat_confidence = 0
        screen = "store"
    elif event_present:
        combat_confidence = 0
        screen = "event_or_text"
    elif hostile_text_present:
        combat_confidence = 20
        screen = "possible_hostile_text"
    else:
        combat_confidence = 0
        screen = "unknown"

    return {
        "screen": screen,
        "enemy_present": enemy_present,
        "combat_confidence": combat_confidence,
        "enemy_blueprints": enemy_blueprints,
        "hostile_text_present": hostile_text_present,
        "store_present": store_present,
        "event_present": event_present,
        "tutorial_present": tutorial_present,
        "weapons_detected": weapons,
        "event_text_candidates": long_texts,
    }


def read_context(path: Path = FTL_CONTINUE_SAVE) -> dict:
    data = read_bytes(path)
    strings = extract_ascii_strings(data)

    context = infer_context(strings)
    context["string_count"] = len(strings)
    context["strings_preview"] = strings[:30]

    return context


def main():
    if not FTL_CONTINUE_SAVE.exists():
        print("[context] continue.sav does not exist.")
        return

    context = read_context(FTL_CONTINUE_SAVE)

    print("[context] Inferred context:")
    print(f"  Screen/state:       {context['screen']}")
    print(f"  Enemy present:      {context['enemy_present']}")
    print(f"  Combat confidence:  {context['combat_confidence']}")
    print(f"  Hostile text found: {context['hostile_text_present']}")
    print(f"  Store present:      {context['store_present']}")
    print(f"  Event present:      {context['event_present']}")
    print(f"  Tutorial/start:     {context['tutorial_present']}")
    print(f"  Strings found:      {context['string_count']}")

    print()
    print("[context] Enemy blueprints:")
    for enemy in context["enemy_blueprints"]:
        print(f"  - {enemy}")

    print()
    print("[context] Weapons detected:")
    for weapon in context["weapons_detected"]:
        print(f"  - {weapon}")

    print()
    print("[context] Possible event text:")
    for text in context["event_text_candidates"]:
        print(f"  - {text}")


if __name__ == "__main__":
    main()