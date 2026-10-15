import csv
from datetime import datetime

from config import LOG_DIR


LOG_FILE = LOG_DIR / "state_log.csv"


FIELDS = [
    "timestamp",
    "method",
    "base_offset",
    "score",

    "hull",
    "fuel",
    "drones",
    "missiles",
    "scrap",
    "unknown_1",
    "unknown_2",

    "delta_hull",
    "delta_fuel",
    "delta_drones",
    "delta_missiles",
    "delta_scrap",
    "delta_unknown_1",
    "delta_unknown_2",

    "detected_event",
]


def ensure_log_file():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not LOG_FILE.exists():
        with open(LOG_FILE, "w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            writer.writeheader()


def delta(previous_values: dict | None, values: dict, key: str) -> int:
    if previous_values is None:
        return 0

    return values[key] - previous_values[key]


def classify_event(
    delta_hull: int,
    delta_fuel: int,
    delta_drones: int,
    delta_missiles: int,
    delta_scrap: int,
    delta_unknown_1: int,
    delta_unknown_2: int,
) -> str:
    resource_deltas = [
        delta_fuel,
        delta_drones,
        delta_missiles,
        delta_scrap,
    ]

    positive_resource_changes = sum(1 for value in resource_deltas if value > 0)
    negative_resource_changes = sum(1 for value in resource_deltas if value < 0)

    if (
        delta_hull == 0
        and delta_fuel == 0
        and delta_drones == 0
        and delta_missiles == 0
        and delta_scrap == 0
    ):
        if delta_unknown_1 != 0 or delta_unknown_2 != 0:
            return "internal_progress_or_map_change"

        return "no_resource_change"

    if delta_fuel == -1 and delta_hull == 0 and delta_scrap == 0:
        return "jump_or_fuel_consumed"

    if delta_hull < 0:
        if positive_resource_changes > 0 or delta_scrap > 0:
            return "damage_received_with_reward"

        return "damage_received"

    if delta_hull > 0:
        if delta_scrap < 0:
            return "repaired_hull"

        return "hull_recovered"

    if delta_fuel == 1 and delta_scrap == -3 and delta_drones == 0 and delta_missiles == 0:
        return "bought_fuel"

    if delta_missiles == 1 and delta_scrap == -6 and delta_fuel == 0 and delta_drones == 0:
        return "bought_missile"

    if delta_drones == 1 and delta_scrap == -8 and delta_fuel == 0 and delta_missiles == 0:
        return "bought_drone"

    if negative_resource_changes > 0 and delta_scrap > 0:
        return "sold_item_or_traded_resources"

    if positive_resource_changes >= 2 and negative_resource_changes == 0:
        return "event_reward_multiple_resources"

    if delta_scrap > 0 and delta_fuel == 0 and delta_drones == 0 and delta_missiles == 0:
        return "scrap_gained_or_item_sold"

    if delta_scrap < 0:
        return "scrap_spent"

    if positive_resource_changes > 0 and delta_scrap >= 0:
        return "event_reward_resources"

    return "unknown_change"


def append_state(values: dict, previous_values: dict | None = None):
    ensure_log_file()

    delta_hull = delta(previous_values, values, "hull")
    delta_fuel = delta(previous_values, values, "fuel")
    delta_drones = delta(previous_values, values, "drones")
    delta_missiles = delta(previous_values, values, "missiles")
    delta_scrap = delta(previous_values, values, "scrap")
    delta_unknown_1 = delta(previous_values, values, "unknown_1")
    delta_unknown_2 = delta(previous_values, values, "unknown_2")

    detected_event = classify_event(
        delta_hull=delta_hull,
        delta_fuel=delta_fuel,
        delta_drones=delta_drones,
        delta_missiles=delta_missiles,
        delta_scrap=delta_scrap,
        delta_unknown_1=delta_unknown_1,
        delta_unknown_2=delta_unknown_2,
    )

    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "method": values["_method"],
        "base_offset": f"0x{values['_base_offset']:08X}",
        "score": values["_score"],

        "hull": values["hull"],
        "fuel": values["fuel"],
        "drones": values["drones"],
        "missiles": values["missiles"],
        "scrap": values["scrap"],
        "unknown_1": values["unknown_1"],
        "unknown_2": values["unknown_2"],

        "delta_hull": delta_hull,
        "delta_fuel": delta_fuel,
        "delta_drones": delta_drones,
        "delta_missiles": delta_missiles,
        "delta_scrap": delta_scrap,
        "delta_unknown_1": delta_unknown_1,
        "delta_unknown_2": delta_unknown_2,

        "detected_event": detected_event,
    }

    with open(LOG_FILE, "a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writerow(row)