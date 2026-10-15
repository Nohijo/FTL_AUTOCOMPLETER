"""Start the next run by itself when one ends (auto-play only).

Game over screen -> its Restart button (GameOver.buttons, labels are text ids such as
"gameover_button_restart"). Main menu -> Continue when a run is saved, else New Game
(answering its "start a new game?" confirmation). Hangar (ShipBuilder) -> Start.
Every click is checked first: the game must report the button as hovered.
"""

import struct
import time

from config import FTL_CONTINUE_SAVE
from observer.memory import layout as L

GAME_OVER_BUTTONS = L.CommandGui.GAME_OVER_SCREEN + 0x20     # vector<TextButton*>
GAME_OVER_TEXT = L.CommandGui.GAME_OVER_SCREEN + 0x70        # string id: gameover_blowup / _crew / _fedbase / _win
TEXT_BUTTON_LABEL = 0xE0                                     # TextButton.label (TextString: string data)
MENU = L.CApp.MENU                                           # MainMenu (embedded)
MENU_CONTINUE = MENU + 0x38                                  # Button
MENU_NEW_GAME = MENU + 0xC8                                  # Button (startButton)
MENU_CONFIRM = MENU + 0x32A0                                 # ConfirmWindow confirmNewGame (bOpen +0x8, yes +0x78)
HANGAR = MENU + 0x448                                        # ShipBuilder
HANGAR_OPEN = HANGAR + 0x10B8                                # bool
HANGAR_START = HANGAR + 0x138                                # TextButton
SETTLE = 2.5                                                 # let screens finish opening before clicking


def game_over_reason(proc) -> str:
    """The game over text id (e.g. "gameover_blowup"), "" if unreadable."""
    try:
        gui = proc.ptr(proc.ptr(L.P_THE_APP) + L.CApp.GUI)
        return proc.string(gui + GAME_OVER_TEXT)
    except Exception:
        return ""


class RestartPilot:
    def __init__(self, game, proc, live: bool):
        self.game, self.proc, self.live = game, proc, live
        self.seen_at: dict[str, float] = {}
        self.next_try = 0.0

    def _button(self, address: int) -> tuple[tuple[int, int, int, int], bool]:
        view = self.proc.view(address, 0x50)
        return struct.unpack_from("<iiii", view.data, L.GenericButton.HITBOX), view.bool(L.GenericButton.ACTIVE)

    def _click(self, address: int, snapshot) -> bool:
        (x, y, w, h), active = self._button(address)
        if not active or w <= 0:
            return False
        self.game.hover(x + w / 2, y + h / 2, snapshot.mapping)
        time.sleep(0.1)
        if not self.proc.bool(address + L.GenericButton.HOVER):
            return False
        self.game.slow_click(x + w / 2, y + h / 2, snapshot.mapping)
        return True

    def _settled(self, screen: str, now: float) -> bool:
        first = self.seen_at.setdefault(screen, now)
        return now - first >= SETTLE

    def step(self, snapshot, now: float) -> list[str]:
        if not self.live or now < self.next_try:
            return []
        app = self.proc.ptr(L.P_THE_APP)
        if snapshot.game_over:
            self.seen_at.pop("menu", None)
            if not self._settled("game_over", now):
                return []
            self.next_try = now + 3
            gui = self.proc.ptr(app + L.CApp.GUI)
            buttons = []
            for address in self.proc.pointer_vector(gui + GAME_OVER_BUTTONS):
                label = self.proc.string(address + TEXT_BUTTON_LABEL).lower()
                buttons.append((label, address))
            for wanted in ("restart", "menu"):
                for label, address in buttons:
                    if wanted in label and self._click(address, snapshot):
                        return [f"[bot] run over ({game_over_reason(self.proc) or 'game over'}): clicked {label}"]
            return [f"[bot] run over: no restart button found (buttons: {[b[0] for b in buttons]})"]
        if snapshot.screen == "main_menu":
            self.seen_at.pop("game_over", None)
            if not self._settled("menu", now):
                return []
            self.next_try = now + 3
            if self.proc.bool(app + HANGAR_OPEN):
                if self._click(app + HANGAR_START, snapshot):
                    return ["[bot] hangar: started the run"]
                return ["[bot] hangar: the Start button was not found"]
            if self.proc.bool(app + MENU_CONFIRM + L.FocusWindow.OPEN):
                if self._click(app + MENU_CONFIRM + 0x78, snapshot):
                    return ["[bot] main menu: confirmed the new game"]
            if FTL_CONTINUE_SAVE.exists() and self._click(app + MENU_CONTINUE, snapshot):
                return ["[bot] main menu: continued the saved run"]
            if self._click(app + MENU_NEW_GAME, snapshot):
                return ["[bot] main menu: new game"]
            return ["[bot] main menu: no button found to start a run"]
        self.seen_at.clear()
        return []
