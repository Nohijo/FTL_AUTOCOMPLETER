import time
from pathlib import Path

from config import FTL_CONTINUE_SAVE
from observer.basic_state_reader import read_basic_state_values
from observer.state_logger import append_state


def get_file_signature(path: Path):
    if not path.exists():
        return None

    stat = path.stat()

    return {
        "modified_time_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def wait_until_file_is_stable(path: Path, checks: int = 3, delay: float = 0.2) -> bool:
    stable_count = 0
    previous_signature = None

    for _ in range(30):
        current_signature = get_file_signature(path)

        if current_signature is None:
            return False

        if current_signature == previous_signature:
            stable_count += 1

            if stable_count >= checks:
                return True
        else:
            stable_count = 0
            previous_signature = current_signature

        time.sleep(delay)

    return False


def visible_values(values: dict) -> dict:
    return {
        "hull": values["hull"],
        "fuel": values["fuel"],
        "drones": values["drones"],
        "missiles": values["missiles"],
        "scrap": values["scrap"],
        "unknown_1": values["unknown_1"],
        "unknown_2": values["unknown_2"],
        "_base_offset": values["_base_offset"],
    }


def format_delta(previous: int, current: int) -> str:
    delta = current - previous

    if delta > 0:
        return f"+{delta}"

    return str(delta)


def print_state(values: dict, previous_values: dict | None = None):
    print()
    print("[live_state] Estado básico detectado:")
    print(f"  Método:   {values['_method']}")
    print(f"  Offset:   0x{values['_base_offset']:08X}")
    print(f"  Score:    {values['_score']}")

    fields = [
        ("Hull", "hull"),
        ("Fuel", "fuel"),
        ("Drones", "drones"),
        ("Missiles", "missiles"),
        ("Scrap", "scrap"),
        ("unk_1", "unknown_1"),
        ("unk_2", "unknown_2"),
    ]

    for label, key in fields:
        current = values[key]

        if previous_values is None:
            print(f"  {label:<8} {current}")
        else:
            previous = previous_values[key]
            delta = current - previous

            if delta == 0:
                print(f"  {label:<8} {current}")
            else:
                print(
                    f"  {label:<8} {previous} -> {current} "
                    f"({format_delta(previous, current)})"
                )


def try_read_state(previous_values: dict | None):
    if not FTL_CONTINUE_SAVE.exists():
        return "NO_CONTINUE", None

    file_is_stable = wait_until_file_is_stable(FTL_CONTINUE_SAVE)

    if not file_is_stable:
        return "UNSTABLE", None

    try:
        values = read_basic_state_values(FTL_CONTINUE_SAVE, previous_values)
        return "OK", values
    except ValueError as error:
        return f"INVALID: {error}", None


def watch_basic_state():
    print("[live_state] Observando estado básico de FTL")
    print(f"[live_state] Save: {FTL_CONTINUE_SAVE}")

    last_signature = get_file_signature(FTL_CONTINUE_SAVE)
    last_values = None
    last_printed = None
    last_status = None

    status, values = try_read_state(last_values)

    if status == "OK" and values is not None:
        print_state(values, last_values)
        append_state(values, last_values)
        last_values = values
        last_printed = visible_values(values)
    else:
        print()
        print("[live_state] No hay estado inicial legible.")

    last_status = status

    try:
        while True:
            current_signature = get_file_signature(FTL_CONTINUE_SAVE)

            if current_signature != last_signature:
                last_signature = current_signature

                status, values = try_read_state(last_values)

                if status != last_status:
                    if status == "NO_CONTINUE":
                        print()
                        print("[live_state] No hay continue.sav. La run terminó o estás en menú.")
                    elif status == "UNSTABLE":
                        print()
                        print("[live_state] continue.sav todavía se está escribiendo. Lectura ignorada.")
                    elif status.startswith("INVALID"):
                        print()
                        print(f"[live_state] Estado no legible: {status}")

                    last_status = status

                if status == "OK" and values is not None:
                    current_printed = visible_values(values)

                    if current_printed != last_printed:
                        print_state(values, last_values)
                        append_state(values, last_values)
                        last_printed = current_printed

                    last_values = values

            time.sleep(0.5)

    except KeyboardInterrupt:
        print()
        print("[live_state] Observación detenida por el usuario.")


if __name__ == "__main__":
    watch_basic_state()