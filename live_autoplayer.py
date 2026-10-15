import time

from config import FTL_CONTINUE_SAVE
from observer.game_state_builder import build_game_state, print_game_state
from observer.live_basic_state import (
    get_file_signature,
    wait_until_file_is_stable,
    visible_values,
)
from brain.decision_engine import decide_next_action, print_decision
from controller.executor import execute_action_dry_run


def action_signature(action) -> tuple:
    return (
        action.name,
        action.priority,
        tuple(sorted(action.data.items())),
    )


def try_build_game_state(previous_basic_state: dict | None):
    if not FTL_CONTINUE_SAVE.exists():
        return "NO_CONTINUE", None

    if not wait_until_file_is_stable(FTL_CONTINUE_SAVE):
        return "UNSTABLE", None

    try:
        game_state = build_game_state(FTL_CONTINUE_SAVE, previous_basic_state)
        return "OK", game_state
    except ValueError as error:
        return f"INVALID: {error}", None


def print_full_autoplayer_update(game_state: dict, action):
    print()
    print("=" * 70)
    print("[autoplayer] New decision cycle")
    print("=" * 70)
    print()

    print_game_state(game_state)
    print_decision(game_state, action)
    execute_action_dry_run(action)


def watch_and_plan():
    print("[live_autoplayer] Watching FTL and producing execution plans")
    print(f"[live_autoplayer] Save: {FTL_CONTINUE_SAVE}")

    last_signature = get_file_signature(FTL_CONTINUE_SAVE)
    last_basic_state = None
    last_visible_state = None
    last_action_signature = None
    last_status = None

    status, game_state = try_build_game_state(last_basic_state)

    if status == "OK" and game_state is not None:
        action = decide_next_action(game_state)
        print_full_autoplayer_update(game_state, action)

        last_basic_state = game_state["basic"]
        last_visible_state = visible_values(last_basic_state)
        last_action_signature = action_signature(action)
    else:
        print()
        print("[live_autoplayer] No readable initial state.")

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
                        print("[live_autoplayer] No continue.sav. The run ended or you are in the menu.")
                    elif status == "UNSTABLE":
                        print()
                        print("[live_autoplayer] continue.sav is still being written.")
                    elif status.startswith("INVALID"):
                        print()
                        print(f"[live_autoplayer] State is not readable: {status}")

                    last_status = status

                if status == "OK" and game_state is not None:
                    basic_state = game_state["basic"]
                    current_visible_state = visible_values(basic_state)

                    action = decide_next_action(game_state)
                    current_action_signature = action_signature(action)

                    state_changed = current_visible_state != last_visible_state
                    action_changed = current_action_signature != last_action_signature

                    if state_changed or action_changed:
                        print_full_autoplayer_update(game_state, action)

                        last_visible_state = current_visible_state
                        last_action_signature = current_action_signature

                    last_basic_state = basic_state

            time.sleep(0.5)

    except KeyboardInterrupt:
        print()
        print("[live_autoplayer] Stopped by user.")


if __name__ == "__main__":
    watch_and_plan()