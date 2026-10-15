import time
from pathlib import Path

from config import FTL_CONTINUE_SAVE
from observer.game_state_builder import build_game_state
from observer.live_basic_state import get_file_signature, wait_until_file_is_stable, visible_values
from brain.decision_engine import decide_next_action, print_decision


def try_build_game_state(previous_values: dict | None):
    if not FTL_CONTINUE_SAVE.exists():
        return "NO_CONTINUE", None

    if not wait_until_file_is_stable(FTL_CONTINUE_SAVE):
        return "UNSTABLE", None

    try:
        game_state = build_game_state(FTL_CONTINUE_SAVE, previous_values)
        return "OK", game_state
    except ValueError as error:
        return f"INVALID: {error}", None


def watch_and_advise():
    print("[advisor] Watching FTL and suggesting decisions")
    print(f"[advisor] Save: {FTL_CONTINUE_SAVE}")

    last_signature = get_file_signature(FTL_CONTINUE_SAVE)
    last_basic_state = None
    last_printed = None
    last_status = None

    status, game_state = try_build_game_state(last_basic_state)

    if status == "OK" and game_state is not None:
        action = decide_next_action(game_state)
        print_decision(game_state, action)

        last_basic_state = game_state["basic"]
        last_printed = visible_values(last_basic_state)
    else:
        print()
        print("[advisor] No readable initial state.")

    last_status = status

    try:
        while True:
            current_signature = get_file_signature(FTL_CONTINUE_SAVE)

            if current_signature != last_signature:
                last_signature = current_signature

                status, game_state = try_build_game_state(last_basic_state)

                if status != last_status:
                    if status == "NO_CONTINUE":
                        print()
                        print("[advisor] No continue.sav. The run ended or you are in the menu.")
                    elif status == "UNSTABLE":
                        print()
                        print("[advisor] continue.sav is still being written.")
                    elif status.startswith("INVALID"):
                        print()
                        print(f"[advisor] State is not readable: {status}")

                    last_status = status

                if status == "OK" and game_state is not None:
                    basic_state = game_state["basic"]
                    current_printed = visible_values(basic_state)

                    if current_printed != last_printed:
                        action = decide_next_action(game_state)
                        print_decision(game_state, action)
                        last_printed = current_printed

                    last_basic_state = basic_state

            time.sleep(0.5)

    except KeyboardInterrupt:
        print()
        print("[advisor] Advisor stopped by user.")


if __name__ == "__main__":
    watch_and_advise()