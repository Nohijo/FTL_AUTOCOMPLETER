import shutil
import time
from datetime import datetime
from pathlib import Path

from config import FTL_CONTINUE_SAVE
from observer.game_state_builder import build_game_state, print_game_state
from observer.live_basic_state import get_file_signature, wait_until_file_is_stable
from brain.decision_engine import decide_next_action, print_decision
from controller.executor import execute_action_dry_run


PROJECT_ROOT = Path(__file__).resolve().parent
SNAPSHOT_DIR = PROJECT_ROOT / "data" / "snapshots"


def safe_label(text: str) -> str:
    allowed = []

    for char in text:
        if char.isalnum() or char in ("_", "-"):
            allowed.append(char)
        else:
            allowed.append("_")

    return "".join(allowed)


def create_snapshot(label: str) -> Path | None:
    if not FTL_CONTINUE_SAVE.exists():
        print("[manual_autoplayer] Cannot snapshot: continue.sav does not exist.")
        return None

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{timestamp}_{safe_label(label)}.sav"
    destination = SNAPSHOT_DIR / filename

    shutil.copy2(FTL_CONTINUE_SAVE, destination)

    print(f"[manual_autoplayer] Snapshot created: {destination}")
    return destination


def wait_for_stable_save():
    if not FTL_CONTINUE_SAVE.exists():
        return False

    return wait_until_file_is_stable(FTL_CONTINUE_SAVE)


def build_decision_cycle():
    game_state = build_game_state()
    action = decide_next_action(game_state)

    print()
    print("=" * 70)
    print("[manual_autoplayer] Decision cycle")
    print("=" * 70)
    print()

    print_game_state(game_state)
    print_decision(game_state, action)
    execute_action_dry_run(action)

    return game_state, action


def main():
    print("[manual_autoplayer] Human-in-the-loop autoplayer")
    print(f"[manual_autoplayer] Save: {FTL_CONTINUE_SAVE}")
    print()
    print("[manual_autoplayer] Controls:")
    print("  ENTER = I performed the suggested action; read the new state")
    print("  s     = take a snapshot only")
    print("  r     = re-read current state")
    print("  q     = quit")
    print()

    cycle_number = 1

    while True:
        if not FTL_CONTINUE_SAVE.exists():
            print("[manual_autoplayer] continue.sav does not exist.")
            print("[manual_autoplayer] Start or continue a run, then press ENTER.")
            command = input("> ").strip().lower()

            if command == "q":
                print("[manual_autoplayer] Exiting.")
                return

            continue

        if not wait_for_stable_save():
            print("[manual_autoplayer] Save file is unstable. Retrying soon.")
            time.sleep(1)
            continue

        previous_signature = get_file_signature(FTL_CONTINUE_SAVE)

        try:
            game_state, action = build_decision_cycle()
        except ValueError as error:
            print(f"[manual_autoplayer] Could not read state: {error}")
            command = input("> ").strip().lower()

            if command == "q":
                print("[manual_autoplayer] Exiting.")
                return

            continue

        create_snapshot(f"cycle_{cycle_number:03d}_before_{action.name}")

        command = input(
            "\n[manual_autoplayer] Perform the plan manually, then press ENTER "
            "(s=snapshot, r=read again, q=quit): "
        ).strip().lower()

        if command == "q":
            print("[manual_autoplayer] Exiting.")
            return

        if command == "s":
            create_snapshot(f"cycle_{cycle_number:03d}_manual_snapshot")
            continue

        if command == "r":
            continue

        if not FTL_CONTINUE_SAVE.exists():
            print("[manual_autoplayer] continue.sav disappeared after action.")
            cycle_number += 1
            continue

        wait_for_stable_save()
        current_signature = get_file_signature(FTL_CONTINUE_SAVE)

        if current_signature == previous_signature:
            print()
            print("[manual_autoplayer] Warning: continue.sav did not change.")
            print("[manual_autoplayer] The game may not have saved the action yet.")
            print("[manual_autoplayer] You can press ENTER again later or use r to re-read.")

        create_snapshot(f"cycle_{cycle_number:03d}_after_{action.name}")

        cycle_number += 1


if __name__ == "__main__":
    main()