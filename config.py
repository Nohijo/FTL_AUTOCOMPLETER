from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent

FTL_INSTALL_DIR = Path.home() / "GOG Games" / "FTL Advanced Edition"
FTL_DATA_DIR = FTL_INSTALL_DIR / "game" / "data"
FTL_DAT_FILE = FTL_DATA_DIR / "ftl.dat"

FTL_SAVE_DIR = Path.home() / ".local" / "share" / "FasterThanLight"
FTL_CONTINUE_SAVE = FTL_SAVE_DIR / "continue.sav"
FTL_PROFILE_SAVE = FTL_SAVE_DIR / "ae_prof.sav"

SNAPSHOT_DIR = PROJECT_DIR / "data" / "snapshots"
DECODED_STATE_DIR = PROJECT_DIR / "data" / "decoded_states"
LOG_DIR = PROJECT_DIR / "data" / "logs"

# Run FTL in its own Xephyr window (game_display.py): the bot plays there without touching
# your real mouse/keyboard, and the game keeps running while you use other windows.
NESTED_DISPLAY = True
# Draw FTL on the GPU (Mesa's Zink: OpenGL over the GPU's Vulkan driver): Xephyr alone only
# offers software OpenGL, which caps FTL's frame rate and so the game speed.
GPU_RENDER = False  # Zink renders on the GPU (~10x) but its frames never reach the Xephyr window (no DRI3)
BACKUP_DIR = PROJECT_DIR / "backups"