import sys
from pathlib import Path

from config import FTL_CONTINUE_SAVE


SYSTEM_LAYOUT_PROFILES = [
    {
        "name": "layout_4761_shield_test",
        "size": 4761,
        "offsets": {
            "shields_level": 0x000003FF,
        },
    },
    {
        "name": "layout_4284_weapons_test",
        "size": 4284,
        "offsets": {
            "weapons_level": 0x00000475,
        },
    },
]


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def read_u8(data: bytes, offset: int) -> int | None:
    if offset >= len(data):
        return None

    return data[offset]


def find_matching_profiles(data: bytes) -> list[dict]:
    file_size = len(data)

    return [
        profile for profile in SYSTEM_LAYOUT_PROFILES
        if profile["size"] == file_size
    ]


def read_system_state(path: Path = FTL_CONTINUE_SAVE) -> dict:
    data = read_bytes(path)
    profiles = find_matching_profiles(data)

    result = {
        "_file_size": len(data),
        "_matched_profiles": [profile["name"] for profile in profiles],
        "systems": {},
    }

    for profile in profiles:
        for system_name, offset in profile["offsets"].items():
            result["systems"][system_name] = {
                "offset": offset,
                "value": read_u8(data, offset),
                "source_profile": profile["name"],
            }

    return result


def print_system_state(path: Path):
    state = read_system_state(path)

    print("[system_state] File:")
    print(f"[system_state] {path}")
    print(f"[system_state] Size: {state['_file_size']} bytes")
    print()

    if not state["_matched_profiles"]:
        print("[system_state] No matching layout profile.")
        print("[system_state] System values are unknown for this save layout.")
        return

    print("[system_state] Matched profiles:")
    for profile_name in state["_matched_profiles"]:
        print(f"  - {profile_name}")

    print()
    print("[system_state] Known system values:")

    for system_name, info in state["systems"].items():
        print(
            f"{system_name:20} "
            f"@ 0x{info['offset']:08X} = {info['value']} "
            f"({info['source_profile']})"
        )


def main():
    if len(sys.argv) >= 2:
        path = Path(sys.argv[1])
    else:
        path = FTL_CONTINUE_SAVE

    if not path.exists():
        print(f"[system_state] File does not exist: {path}")
        return

    print_system_state(path)


if __name__ == "__main__":
    main()