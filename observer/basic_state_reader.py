import struct
import sys
from pathlib import Path

from config import FTL_CONTINUE_SAVE


FIELD_ORDER = [
    "hull",
    "fuel",
    "drones",
    "missiles",
    "scrap",
    "unknown_1",
    "unknown_2",
]


VALID_RANGES = {
    "hull": (1, 30),
    "fuel": (0, 99),
    "drones": (0, 99),
    "missiles": (0, 99),
    "scrap": (0, 999),
    "unknown_1": (-1000, 1000),
    "unknown_2": (-1000, 1000),
}


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def read_int32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def read_state_at_offset(data: bytes, base_offset: int) -> dict:
    values = {}

    for index, name in enumerate(FIELD_ORDER):
        offset = base_offset + index * 4
        values[name] = read_int32(data, offset)

    values["_base_offset"] = base_offset
    return values


def is_plausible_state(values: dict) -> bool:
    for name in FIELD_ORDER:
        value = values[name]
        minimum, maximum = VALID_RANGES[name]

        if not minimum <= value <= maximum:
            return False

    return True


def basic_score(values: dict) -> int:
    score = 0

    if values["hull"] == 30:
        score += 30

    if 1 <= values["hull"] <= 30:
        score += 20

    if 0 <= values["fuel"] <= 30:
        score += 20

    if 0 <= values["drones"] <= 30:
        score += 15

    if 0 <= values["missiles"] <= 30:
        score += 15

    if 0 <= values["scrap"] <= 300:
        score += 20

    if 0 <= values["unknown_1"] <= 20:
        score += 5

    if 0 <= values["unknown_2"] <= 20:
        score += 5

    return score


def transition_score(candidate: dict, previous: dict | None) -> int:
    if previous is None:
        return basic_score(candidate)

    score = basic_score(candidate)

    previous_offset = previous.get("_base_offset")

    if previous_offset is not None:
        offset_distance = abs(candidate["_base_offset"] - previous_offset)
        score -= offset_distance // 2

    hull_change = candidate["hull"] - previous["hull"]
    fuel_change = candidate["fuel"] - previous["fuel"]
    drones_change = candidate["drones"] - previous["drones"]
    missiles_change = candidate["missiles"] - previous["missiles"]
    scrap_change = candidate["scrap"] - previous["scrap"]

    if abs(hull_change) <= 5:
        score += 40
    else:
        score -= 80

    if abs(fuel_change) <= 10:
        score += 30
    else:
        score -= 60

    if abs(drones_change) <= 10:
        score += 20
    else:
        score -= 50

    if abs(missiles_change) <= 10:
        score += 20
    else:
        score -= 50

    if abs(scrap_change) <= 100:
        score += 20
    else:
        score -= 50

    if previous["hull"] > 0 and candidate["hull"] == 0:
        score -= 200

    return score


def find_state_candidates(data: bytes) -> list[dict]:
    candidates = []

    start = 0x80
    end = min(len(data) - len(FIELD_ORDER) * 4, 0x400)

    for base_offset in range(start, end):
        try:
            values = read_state_at_offset(data, base_offset)
        except struct.error:
            continue

        if is_plausible_state(values):
            values["_score"] = basic_score(values)
            candidates.append(values)

    candidates.sort(key=lambda item: item["_score"], reverse=True)
    return candidates


def read_basic_state_values(path: Path, previous_values: dict | None = None) -> dict:
    data = read_bytes(path)
    candidates = find_state_candidates(data)

    if not candidates:
        raise ValueError("No se encontró un bloque plausible de estado básico.")

    for candidate in candidates:
        candidate["_score"] = transition_score(candidate, previous_values)

    candidates.sort(key=lambda item: item["_score"], reverse=True)

    best = candidates[0]
    best["_method"] = "scan"

    return best


def print_basic_state(path: Path):
    values = read_basic_state_values(path)

    print("[basic_state] Archivo:")
    print(f"[basic_state] {path}")
    print(f"[basic_state] Tamaño: {path.stat().st_size} bytes")
    print()

    print(f"[basic_state] Método: {values['_method']}")
    print(f"[basic_state] Base offset: 0x{values['_base_offset']:08X}")
    print(f"[basic_state] Score: {values['_score']}")
    print()

    for name in FIELD_ORDER:
        offset = values["_base_offset"] + FIELD_ORDER.index(name) * 4
        value = values[name]
        print(f"{name:10} @ 0x{offset:08X} = {value}")


def main():
    if len(sys.argv) >= 2:
        path = Path(sys.argv[1])
    else:
        path = FTL_CONTINUE_SAVE

    if not path.exists():
        print(f"[basic_state] No existe el archivo: {path}")
        return

    try:
        print_basic_state(path)
    except ValueError as error:
        print(f"[basic_state] {error}")


if __name__ == "__main__":
    main()