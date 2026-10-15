from config import FTL_CONTINUE_SAVE
from observer.game_state_builder import build_game_state, print_game_state
from brain.decision_engine import decide_next_action, print_decision
from controller.executor import execute_action_dry_run


def main():
    if not FTL_CONTINUE_SAVE.exists():
        print("[autoplayer] continue.sav does not exist. Start or continue a run first.")
        return

    game_state = build_game_state()
    action = decide_next_action(game_state)

    print_game_state(game_state)
    print_decision(game_state, action)
    execute_action_dry_run(action)


if __name__ == "__main__":
    main()