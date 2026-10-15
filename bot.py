"""Live FTL bot: reads game memory through ftl_host and reacts.

    ftl-bot                        dry run: print what the bot would do
    ftl-bot --live                 send the input to FTL

If FTL is not running yet, it is started through ftl-host in the background.
It also starts the browser dashboard (http://127.0.0.1:8765, see dashboard.py),
which shows this output live next to the ship's status and holds the switches
(auto-play, game speed, overlay), and the overlay that shows the bot's
status over the game (overlay.py); --no-dash skips both.

Auto-play (F9 in the game, or the dashboard) decides who plays. On: the bot plays,
sending its clicks and keys straight to FTL's window (never moving your mouse or
typing into other windows), also while FTL is behind other windows. Off: manual
control; the bot only watches and records.

What it does today:
  - powers weapons and aims them at the enemy system chosen by
    brain/combat_policy, firing guns together as a volley and missiles when
    the policy allows them; if the enemy tries to flee, it hits their helm
    or engines instead
  - sends the nearest free crew member to rooms with fires or hull breaches
  - picks event choices by scoring what each one actually leads to
    (brain/event_chooser.py); --no-events leaves dialogs to you
  - gives every crew member a station and sends them back after each job
  - jumps on its own once crew are healed and repairs are done, choosing the
    beacon by its event, the way to the exit, fuel and the rebel fleet, and
    picks the next sector at the exit (brain/navigator.py); --no-jump turns
    that off
  - at a store, buys hull repairs, fuel, missiles and drone parts as needed
    (brain/shopper.py); --no-shop leaves stores to you
  - pauses and warns when the hull is critical and the FTL drive is charged
Outside combat it prints the strategic advice of brain/decision_engine.
"""

import argparse
import struct
import sys
import time
import webbrowser

import bot_settings
from brain.decision_engine import decide_next_action
from brain.event_chooser import best_choice, outcome_value, score_event
from brain.navigator import choose_destination, choose_sector, jump_blockers
from brain.shopper import appraise, next_purchase
from brain.upgrader import plan_upgrade
from brain.ship_profile import adapt_upgrade_steps, profile_for
from brain.augments import describe as describe_augments
from brain.flagship import is_flagship
from brain.arsenal import plan_equipment_move, sell_candidates
from brain.live_combat import Intent, LiveCombatBrain, heading_to
from config import FTL_SAVE_DIR, LOG_DIR
from controller.game_input import FocusLost, GameInput
from observer.run_log import RunRecorder
from controller.restart import RestartPilot, game_over_reason
from dashboard import URL as DASHBOARD_URL, dashboard_is_up, ensure_dashboard
from overlay import ensure_overlay
from ftl_host import SOCKET_PATH, ensure_host
from observer.memory import layout as L
from observer.memory.adapter import in_combat, to_legacy_game_state
from observer.memory.events import read_event_on_screen, read_outcome
from observer.memory.starmap import read_star_map
from observer.memory.equipment import read_equipment
from observer.memory.store import current_store_address, read_store
from observer.memory.upgrades import read_upgrades
from observer.memory.process import AttachError, HostClient, MemoryReadError
from observer.memory.reader import GameSnapshot, read_snapshot, weapon_armed


def send_crew(intent: Intent, game: GameInput, proc, snapshot: GameSnapshot, live: bool) -> tuple[bool, str | None]:
    """Select by hotkey, confirm the selection, order, confirm the crew member is heading there."""
    if not game.select_crew(intent.crew, snapshot):
        return False, "no crew hotkey for this crew member"
    if live:
        time.sleep(0.1)
        selected = read_snapshot(proc).selected_crew
        if intent.crew.address not in selected:
            return False, f"selection failed ({len(selected)} crew selected)"
    game.order_selected_crew_to_room(intent.room, snapshot)
    if not live:
        return True, None
    time.sleep(0.12)
    crew = next((c for c in read_snapshot(proc).player.crew if c.address == intent.crew.address), None)
    if crew is not None and heading_to(crew, intent.room):
        return True, "ok: on the way"
    return False, "order did not take"


class EventPilot:
    """Waits for an event dialog to settle, scores its choices and picks the best one."""

    SETTLE_SECONDS = 1.5   # a dialog ignores keys while it is still appearing

    def __init__(self, game: GameInput, proc, live: bool):
        self.game, self.proc, self.live = game, proc, live
        self.signature = None
        self.first_seen = 0.0
        self.handled = None

    def step(self, snapshot: GameSnapshot, now: float) -> list[str]:
        # At a store beacon FTL opens the store behind the arrival dialog, so an open store
        # must not stop us from answering the dialog that is on top of it.
        if not snapshot.event_open or snapshot.menu_open:
            self.signature = None
            return []
        event = read_event_on_screen(self.proc)
        if event is None or not event.choices:
            return []
        signature = event.signature()
        if signature != self.signature or snapshot.player.jumping or snapshot.player.jump_animating:
            # Count the settle time from the end of the arrival animation.
            self.signature, self.first_seen = signature, now
            return []
        if now - self.first_seen < self.SETTLE_SECONDS or signature == self.handled:
            return []
        self.handled = signature

        scored = score_event(event, snapshot.player, snapshot.sector or 1)
        best = best_choice(scored)
        lines = [f"[bot] EVENT: {event.text.replace(chr(10), ' ')[:200]}"]
        for choice in scored:
            mark = "*" if best is not None and choice.index == best.index else " "
            score = "  n/a " if choice.score == float("-inf") else f"{choice.score:+6.1f}"
            greyed = "" if choice.selectable else " (not selectable)"
            lines.append(f"      {mark} {choice.index + 1}. [{score}] {choice.text[:70]}{greyed}  -> {choice.summary}")
        if best is None:
            lines.append("[bot] event: no selectable choice; leaving it to you")
            return lines
        if not self.live:
            lines.append(f"[bot] event: would pick {best.index + 1}")
            return lines

        self.game.press_key(str(best.index + 1), snapshot)
        time.sleep(0.4)
        after = read_event_on_screen(self.proc)
        if after is not None and after.signature() == signature and best.index < len(event.shown_rects):
            # Number keys did not take (dialog_keys off?): click the choice instead.
            x, y, w, h = event.shown_rects[best.index]
            self.game.click(x + w / 2, y + h / 2, snapshot.mapping)
            time.sleep(0.4)
            after = read_event_on_screen(self.proc)
        moved_on = after is None or after.signature() != signature
        lines.append(f"[bot] event: picked {best.index + 1} -> {'ok' if moved_on else 'the dialog did not change'}")
        if not moved_on:
            self.handled = None  # try again next time
        return lines


def close_window(game: GameInput, proc, snapshot: GameSnapshot, which: str) -> bool:
    """Press Escape only if `which` window ("ship_screen", "store" or "map") is open right now.
    With nothing open, Escape would bring up FTL's pause menu instead."""
    current = read_snapshot(proc)
    is_open = {"ship_screen": current.ship_screen_open, "store": current.store_open,
               "map": current.map_open}[which]
    if not is_open:
        return False
    game.press_key("Escape", snapshot)
    game.last_escape = time.monotonic()
    time.sleep(0.3)
    return True


def drag_item(game: GameInput, proc, snapshot: GameSnapshot, source, target, offsets) -> bool:
    """Drag an equipment item from one box onto another (they swap), or onto a point (the
    store's sell box). Finds the real position by hovering until the game marks the box
    hovered. Returns False when the source box could not be found under the mouse."""
    start = None
    for ox, oy in offsets:
        cx, cy = source.center[0] + ox, source.center[1] + oy
        game.hover(cx, cy, snapshot.mapping)
        time.sleep(0.08)
        fresh = read_equipment(proc).find(source.address)
        if fresh is not None and fresh.hover:
            start = (cx, cy, ox, oy)
            break
    if start is None:
        return False
    cx, cy, ox, oy = start
    if isinstance(target, tuple):
        tx, ty = target[0] + ox, target[1] + oy
    else:
        tx, ty = target.center[0] + ox, target.center[1] + oy
    game._xdotool("mousedown", "1")
    for step in range(1, 7):              # in steps, so the game sees a drag and not a click
        t = step / 6
        game.hover(cx + (tx - cx) * t, cy + (ty - cy) * t, snapshot.mapping)
        time.sleep(0.03)
    time.sleep(0.05)
    game._xdotool("mouseup", "1")
    time.sleep(0.25)
    return True


class EquipmentPilot:
    """Keep the best weapons mounted (brain/arsenal.py): out of combat, open the ship's
    Equipment tab, drag weapons between the cargo hold and the slots (the ones to power
    first in the first slots), mount spare drones, and decide what stays behind when a
    reward does not fit. Every drag is checked by reading the boxes again."""

    TAB = 2                                # ship screen tabs: upgrades, crew, equipment
    MAX_MOVES = 8

    def __init__(self, game: GameInput, proc, live: bool):
        self.game, self.proc, self.live = game, proc, live
        self.retry_after = 0.0
        self.next_check = 0.0
        self.checked = None                # (weapons, cargo, scrap, sector) last found in order
        self.visiting = False

    def _signature(self, snapshot: GameSnapshot, equipment) -> tuple:
        return (tuple(b.item for b in equipment.boxes), snapshot.player.scrap, snapshot.sector,
                snapshot.player.systems["weapons"].level if "weapons" in snapshot.player.systems else 0)

    def pending(self, snapshot: GameSnapshot) -> bool:
        if snapshot.player is None or in_combat(snapshot) or time.monotonic() < self.retry_after:
            return False
        if not snapshot.ship_screen_open and time.monotonic() < self.next_check:
            return False
        self.next_check = time.monotonic() + 2.0
        try:
            equipment = read_equipment(self.proc)
        except MemoryReadError:
            return False
        if equipment.over_capacity and snapshot.ship_screen_open:
            return True                    # a reward that does not fit: triage it now
        if snapshot.ship_screen_open or equipment.store_mode and snapshot.store_open:
            return False
        signature = self._signature(snapshot, equipment)
        if signature == self.checked:
            return False
        if plan_equipment_move(equipment, snapshot) is None:
            self.checked = signature
            return False
        return True

    def _open(self, snapshot: GameSnapshot) -> bool:
        gui = self.proc.ptr(self.proc.ptr(L.P_THE_APP) + L.CApp.GUI)
        if not read_snapshot(self.proc).ship_screen_open:
            self.game.press("ship_inv", snapshot)
            time.sleep(0.5)
        if not read_snapshot(self.proc).ship_screen_open:
            return False
        tabbed = gui + L.CommandGui.SHIP_SCREENS
        if self.proc.i32(tabbed + L.TabbedWindow.CURRENT_TAB) != self.TAB:
            tabs = self.proc.pointer_vector(tabbed + L.TabbedWindow.BUTTONS)
            if len(tabs) > self.TAB:
                x, y, w, h = struct.unpack("<iiii", self.proc.read(tabs[self.TAB] + L.GenericButton.HITBOX, 16))
                self.game.click(x + w / 2, y + h / 2, snapshot.mapping)
                time.sleep(0.4)
        return self.proc.i32(tabbed + L.TabbedWindow.CURRENT_TAB) == self.TAB

    def step(self, snapshot: GameSnapshot) -> list[str]:
        equipment = read_equipment(self.proc)
        move = plan_equipment_move(equipment, snapshot)
        if not self.live:
            self.checked = self._signature(snapshot, equipment)
            return [f"[bot] equipment: would {move[2]}"] if move else []
        lines = []
        opened_by_us = not snapshot.ship_screen_open
        if not self._open(snapshot):
            self.retry_after = time.monotonic() + 60
            close_window(self.game, self.proc, snapshot, "ship_screen")
            return ["[bot] equipment: could not open the Equipment tab"]
        self.visiting = True
        gui = self.proc.ptr(self.proc.ptr(L.P_THE_APP) + L.CApp.GUI)
        window = self.proc.view(gui + L.CommandGui.EQUIP_SCREEN, 0x20).pair_i(0x18)
        try:
            for _ in range(self.MAX_MOVES):
                current = read_snapshot(self.proc)
                equipment = read_equipment(self.proc)
                move = plan_equipment_move(equipment, current)
                if move is None:
                    break
                source, target, why = move
                moved = source.item
                if not drag_item(self.game, self.proc, current, source, target, ((0, 0), window)):
                    lines.append(f"[bot] equipment: could not find {source.item} under the mouse; stopping")
                    self.retry_after = time.monotonic() + 60
                    break
                after = read_equipment(self.proc)
                landed = after.find(target.address)
                if landed is None or landed.item != moved:
                    lines.append(f"[bot] equipment: dragging {moved} did not move it; stopping")
                    self.retry_after = time.monotonic() + 60
                    break
                lines.append(f"[bot] equipment: {why} -> ok")
        finally:
            self.visiting = False
            if opened_by_us or read_equipment(self.proc).over_capacity:
                close_window(self.game, self.proc, snapshot, "ship_screen")
        final = read_equipment(self.proc)
        self.checked = self._signature(read_snapshot(self.proc), final)
        mounted = [b.item or "-" for b in final.of("weapon") if not b.blocked]
        cargo = [b.item for b in final.of("cargo") if b.item]
        return lines + [f"[bot] equipment: weapons {', '.join(mounted)}; cargo {', '.join(cargo) or 'empty'}"]


class ShopPilot:
    """At a store beacon: open the store, buy what brain/shopper.py asks for, close it."""

    MAX_PURCHASES = 40

    def __init__(self, game: GameInput, proc, live: bool):
        self.game, self.proc, self.live = game, proc, live
        self.done: set[int] = set()       # stores already handled
        self.failures: dict[int, int] = {}

    def pending(self, snapshot: GameSnapshot) -> bool:
        if snapshot.store_button is None:
            return False
        store = current_store_address(self.proc)
        return bool(store) and store not in self.done

    def _hover(self, rect, offsets, hovered, snapshot: GameSnapshot) -> tuple[float, float] | None:
        """Find where a button really is: its hitbox as absolute coordinates, or relative to
        one of its windows. Whichever the game reports as hovered wins."""
        x, y, w, h = rect
        for ox, oy in offsets:
            cx, cy = ox + x + w / 2, oy + y + h / 2
            self.game.hover(cx, cy, snapshot.mapping)
            time.sleep(0.08)
            fresh = read_store(self.proc)
            if fresh is not None and hovered(fresh):
                return cx, cy
        return None

    def _hover_item(self, item, store, snapshot: GameSnapshot) -> tuple[float, float] | None:
        def hovered(fresh):
            again = fresh.find(item.address)
            return again is not None and again.hover
        return self._hover(item.rect, ((0, 0), store.position), hovered, snapshot)

    def _show_page(self, page: int, store, snapshot: GameSnapshot) -> bool:
        """Weapons, drones and augments can sit on the store's second page."""
        if page in (0, store.page):
            return True
        rect, _ = store.page_buttons[page]
        spot = self._hover(rect, ((0, 0), store.position), lambda fresh: fresh.page_buttons[page][1], snapshot)
        if spot is None:
            return False
        self.game.click(*spot, snapshot.mapping)
        time.sleep(0.25)
        fresh = read_store(self.proc)
        return fresh is not None and fresh.page == page

    def _tab(self, index: int, snapshot: GameSnapshot) -> bool:
        """The store window's tabs: 0 = the store, 1 = Sell (the ship's equipment)."""
        gui = self.proc.ptr(self.proc.ptr(L.P_THE_APP) + L.CApp.GUI)
        tabbed = gui + L.CommandGui.STORE_SCREENS
        if self.proc.i32(tabbed + L.TabbedWindow.CURRENT_TAB) == index:
            return True
        tabs = self.proc.pointer_vector(tabbed + L.TabbedWindow.BUTTONS)
        if len(tabs) <= index:
            return False
        x, y, w, h = struct.unpack("<iiii", self.proc.read(tabs[index] + L.GenericButton.HITBOX, 16))
        self.game.click(x + w / 2, y + h / 2, snapshot.mapping)
        time.sleep(0.4)
        return self.proc.i32(tabbed + L.TabbedWindow.CURRENT_TAB) == index

    def _equipment_pass(self, snapshot: GameSnapshot, lines: list[str], arrange: bool):
        """On the Sell tab: mount the best weapons (brain/arsenal.py), then sell the cargo the
        ship has no use for by dropping it on the sell box. Each step is checked."""
        current = read_snapshot(self.proc)
        moves = arrange and plan_equipment_move(read_equipment(self.proc), current) is not None
        if not moves and not sell_candidates(read_equipment(self.proc), current):
            return
        if not self._tab(1, snapshot):
            lines.append("[bot] store: could not open the Sell tab")
            return
        gui = self.proc.ptr(self.proc.ptr(L.P_THE_APP) + L.CApp.GUI)
        window = self.proc.view(gui + L.CommandGui.EQUIP_SCREEN, 0x20).pair_i(0x18)
        offsets = ((0, 0), window)
        try:
            for _ in range(EquipmentPilot.MAX_MOVES if arrange else 0):
                current = read_snapshot(self.proc)
                move = plan_equipment_move(read_equipment(self.proc), current)
                if move is None:
                    break
                source, target, why = move
                if not drag_item(self.game, self.proc, current, source, target, offsets) \
                        or (read_equipment(self.proc).find(target.address) or source).item != source.item:
                    lines.append(f"[bot] store: could not move {source.item}; stopping")
                    break
                lines.append(f"[bot] equipment: {why} -> ok")
            for _ in range(6):
                current = read_snapshot(self.proc)
                equipment = read_equipment(self.proc)
                candidates = sell_candidates(equipment, current)
                if not candidates:
                    break
                box, price, why = candidates[0]
                drop = equipment.overflow.center if equipment.overflow is not None else \
                    (equipment.sell_box[0] + 136, equipment.sell_box[1] + 150)
                before = current.player.scrap
                if not drag_item(self.game, self.proc, current, box, drop, offsets):
                    lines.append(f"[bot] store: could not find {box.item} to sell it; stopping")
                    break
                gained = read_snapshot(self.proc).player.scrap - before
                if gained <= 0:
                    lines.append(f"[bot] store: dropping {box.item} on the sell box did not sell it; stopping")
                    break
                lines.append(f"[bot] store: sold {box.item} for {gained} scrap ({why})")
        finally:
            self._tab(0, snapshot)

    def _confirm(self, snapshot: GameSnapshot) -> bool:
        """Answer Yes when a purchase asks for confirmation."""
        store = read_store(self.proc)
        if store is None or not store.confirm_open:
            return True
        rect, _ = store.confirm_yes
        spot = self._hover(rect, ((0, 0), store.confirm_position, store.position),
                           lambda fresh: fresh.confirm_yes[1], snapshot)
        if spot is None:
            return False
        self.game.click(*spot, snapshot.mapping)
        time.sleep(0.3)
        fresh = read_store(self.proc)
        return fresh is not None and not fresh.confirm_open

    def step(self, snapshot: GameSnapshot) -> list[str]:
        if not self.pending(snapshot):
            return []
        store_address = current_store_address(self.proc)
        store = read_store(self.proc)
        if not self.live:
            plan = next_purchase(store, snapshot) if store else None
            self.done.add(store_address)
            return [f"[bot] STORE here; would buy: {plan[0].name} ({plan[0].cost} scrap) - {plan[1]}"
                    if plan else "[bot] STORE here; nothing needed"]

        lines = []
        if not snapshot.store_open:
            x, y, w, h = snapshot.store_button
            self.game.click(x + w / 2, y + h / 2, snapshot.mapping)
            time.sleep(0.5)
            if not read_snapshot(self.proc).store_open:
                count = self.failures[store_address] = self.failures.get(store_address, 0) + 1
                if count >= 3:
                    self.done.add(store_address)
                return ["[bot] store: the store button did not open the store"]
            lines.append("[bot] STORE opened")

        # Sell what the ship has no use for first: the scrap may buy something better.
        self._equipment_pass(snapshot, lines, arrange=False)
        stock = appraise(read_store(self.proc), read_snapshot(self.proc), read_equipment(self.proc))
        lines += [f"[bot] STORE stock, {line}" for line in stock]
        for _ in range(self.MAX_PURCHASES):
            current = read_snapshot(self.proc)
            store = read_store(self.proc)
            plan = next_purchase(store, current, read_equipment(self.proc)) if store else None
            if plan is None:
                break
            item, reason = plan
            if not self._show_page(item.page, store, current):
                lines.append(f"[bot] store: could not turn to page {item.page} for {item.name}; stopping")
                break
            store = read_store(self.proc)
            item = store.find(item.address) or item
            spot = self._hover_item(item, store, current)
            if spot is None:
                lines.append(f"[bot] store: could not find the {item.name} button under the mouse; stopping")
                break
            scrap_before = current.player.scrap
            self.game.click(*spot, current.mapping)
            time.sleep(0.35)
            if not self._confirm(current):
                lines.append(f"[bot] store: {item.name} asked for confirmation and Yes did not work; stopping")
                break
            scrap_after = read_snapshot(self.proc).player.scrap
            if scrap_after < scrap_before:
                lines.append(f"[bot] store: bought {item.name} for {scrap_before - scrap_after} scrap ({reason})")
            else:
                lines.append(f"[bot] store: clicking {item.name} did not buy it; stopping")
                break

        # A weapon bought into the cargo hold: mount it, then sell the one it replaced.
        self._equipment_pass(snapshot, lines, arrange=True)
        close_window(self.game, self.proc, snapshot, "store")
        closed = not read_snapshot(self.proc).store_open
        self.done.add(store_address)
        return lines + [f"[bot] STORE done{'' if closed else ' (the store is still open)'}; "
                        f"scrap left {read_snapshot(self.proc).player.scrap}"]


class UpgradePilot:
    """Open the ship screen, buy the upgrades brain/upgrader.py asks for, close it (which applies them)."""

    def __init__(self, game: GameInput, proc, live: bool):
        self.game, self.proc, self.live = game, proc, live
        self.last_scrap = None             # scrap amount whose upgrades are fully handled
        self.visiting = False              # the ship screen is open because the bot opened it
        self.profile = None                # brain/ship_profile.ShipProfile of the ship being flown
        self.open_failures = 0
        self.retry_after = 0.0             # back off after the screen repeatedly fails to open

    def interrupted(self):
        """The visit was cut short (e.g. FTL lost focus): try again next time."""
        self.last_scrap = None
        self.visiting = False

    def pending(self, snapshot: GameSnapshot) -> bool:
        if snapshot.player is None:
            return False
        if snapshot.ship_screen_open:
            # Finish a visit of the bot's that was cut short; a screen the player opened is theirs.
            return self.visiting and self.live
        if snapshot.player.scrap == self.last_scrap or time.monotonic() < self.retry_after:
            return False
        state = read_upgrades(self.proc)
        if state is None or plan_upgrade(state, snapshot.player, self.profile) is None:
            self.last_scrap = snapshot.player.scrap
            return False
        return True

    def _hover(self, rect, position, check, snapshot) -> tuple[float, float] | None:
        x, y, w, h = rect
        for ox, oy in ((0, 0), position):
            cx, cy = ox + x + w / 2, oy + y + h / 2
            self.game.hover(cx, cy, snapshot.mapping)
            time.sleep(0.08)
            if check(read_upgrades(self.proc)):
                return cx, cy
        return None

    def _open_upgrades(self, snapshot: GameSnapshot):
        """Open the ship screen on its Upgrades tab: FTL's hotkey first, the HUD button as
        a fallback, then the Upgrades tab if the screen remembered another one."""
        gui = self.proc.ptr(self.proc.ptr(L.P_THE_APP) + L.CApp.GUI)
        if not read_snapshot(self.proc).ship_screen_open:
            self.game.press("ship_info", snapshot)
            time.sleep(0.5)
        if not read_snapshot(self.proc).ship_screen_open:
            x, y, w, h = struct.unpack("<iiii", self.proc.read(
                gui + L.CommandGui.UPGRADE_BUTTON + L.GenericButton.HITBOX, 16))
            self.game.click(x + w / 2, y + h / 2, snapshot.mapping)
            time.sleep(0.5)
        state = read_upgrades(self.proc)
        if state is not None and not state.open and read_snapshot(self.proc).ship_screen_open:
            tabs = self.proc.pointer_vector(gui + L.CommandGui.SHIP_SCREENS + L.TabbedWindow.BUTTONS)
            if tabs:
                x, y, w, h = struct.unpack("<iiii", self.proc.read(tabs[0] + L.GenericButton.HITBOX, 16))
                self.game.click(x + w / 2, y + h / 2, snapshot.mapping)
                time.sleep(0.4)
                state = read_upgrades(self.proc)
        return state

    def step(self, snapshot: GameSnapshot) -> list[str]:
        state = read_upgrades(self.proc)
        plan = plan_upgrade(state, snapshot.player, self.profile) if state else None
        if plan is None:
            self.last_scrap = snapshot.player.scrap
            if snapshot.ship_screen_open and self.live and self.visiting:
                close_window(self.game, self.proc, snapshot, "ship_screen")   # nothing to buy: do not leave it open
                self.visiting = False
                return ["[bot] upgrade: nothing to buy; closed the ship screen"]
            return []
        if not self.live:
            return [f"[bot] UPGRADE: would buy {plan[3]} for {plan[2]} scrap"]

        lines = []
        self.visiting = True
        state = self._open_upgrades(snapshot)
        if state is None or not state.open:
            close_window(self.game, self.proc, snapshot, "ship_screen")
            self.visiting = False
            self.open_failures += 1
            if self.open_failures >= 3:
                self.open_failures, self.retry_after = 0, time.monotonic() + 60
                return ["[bot] upgrade: could not open the upgrade screen 3 times; trying again in 60 s"]
            return ["[bot] upgrade: could not open the upgrade screen"]
        self.open_failures = 0

        for _ in range(10):
            current = read_snapshot(self.proc)
            plan = plan_upgrade(state, current.player, self.profile)
            if plan is None:
                break
            what, option, cost, reason = plan
            if option is None:
                spot = self._hover(state.reactor_rect, state.position, lambda s: s and s.reactor_hover, current)
            else:
                spot = self._hover(option.rect, state.position,
                                   lambda s, a=option.address: s and s.find(a) and s.find(a).hover, current)
            if spot is None:
                lines.append(f"[bot] upgrade: could not find the {what} button under the mouse; stopping")
                break
            def pending(s) -> int:
                return s.reactor_pending if option is None else s.find(option.address).pending

            scrap_before, pending_before = current.player.scrap, pending(state)
            self.game.click(*spot, current.mapping)
            time.sleep(0.3)
            state = read_upgrades(self.proc)
            gained = pending(state) - pending_before
            undone = 0
            # One click has been seen to add several levels at once; give back the extras
            # (a right click refunds one pending level) so the reserve is respected.
            while gained - undone > 1 and undone < 5:
                self.game.click(*spot, current.mapping, button=3)
                time.sleep(0.25)
                state = read_upgrades(self.proc)
                undone = gained - (pending(state) - pending_before)
            scrap_after = read_snapshot(self.proc).player.scrap
            if scrap_after >= scrap_before:
                lines.append(f"[bot] upgrade: clicking {what} did not buy it; stopping")
                break
            extra = f"; the click added {gained} levels, undid {undone}" if gained > 1 else ""
            lines.append(f"[bot] upgrade: {reason} ({scrap_before - scrap_after} scrap{extra})")

        close_window(self.game, self.proc, snapshot, "ship_screen")   # Upgrades::Close applies the pending levels
        after = read_snapshot(self.proc)
        if after.ship_screen_open:
            return lines + ["[bot] upgrade: the ship screen did not close; will try again"]
        self.visiting = False
        self.last_scrap = after.player.scrap
        levels = ", ".join(f"{name} {system.level}" for name, system in after.player.systems.items()
                           if name in ("shields", "engines", "weapons", "pilot", "oxygen", "medbay"))
        return lines + [f"[bot] UPGRADES applied ({levels}; reactor {after.player.reactor_total}); "
                        f"scrap left {self.last_scrap}"]


class RewardWatch:
    """After a fight is won, hold every jump (and every window the bot would open) until FTL
    has shown the reward and added it. The reward event only appears once the wreck has
    finished exploding (Ship::DestroyedDone), or a moment after the last enemy crew member
    dies; jumping before that loses it. Claimed when the reward dialog has opened and closed;
    the resources gained are checked and logged."""

    TIMEOUT = 25.0                         # no reward dialog after this long: stop waiting

    def __init__(self):
        self.pending: dict | None = None
        self.handled = False               # the current wreck's reward is done
        self.crew_seen = 0                 # most enemy crew seen aboard in this fight
        self.no_crew = 0                   # observations in a row with no enemy crew aboard

    @staticmethod
    def _resources(player) -> dict:
        return {"scrap": player.scrap, "fuel": player.fuel, "missiles": player.missiles,
                "drone parts": player.drone_parts}

    @staticmethod
    def _aboard(enemy) -> int:
        return sum(1 for c in enemy.crew if not c.intruder and not c.dead)

    def observe(self, snapshot: GameSnapshot, now: float) -> list[str]:
        enemy, player = snapshot.enemy, snapshot.player
        if player is None:
            return []
        if in_combat(snapshot):
            self.handled, self.pending = False, None       # a fight on: its reward comes later
            self.crew_seen = max(self.crew_seen, self._aboard(enemy))
            return []
        if self.pending is None:
            if enemy is None:
                self.handled, self.crew_seen, self.no_crew = False, 0, 0
                return []
            if self.handled:
                return []
            destroyed = enemy.destroyed or enemy.hull <= 0
            self.no_crew = getattr(self, "no_crew", 0) + 1 if self._aboard(enemy) == 0 else 0
            crew_killed = self.crew_seen > 0 and self.no_crew >= 2 and not enemy.surrendered
            if not (destroyed or crew_killed):
                return []
            self.pending = {"enemy": enemy.blueprint, "since": now, "before": self._resources(player),
                            "how": "destroyed" if destroyed else "crew killed", "dialog": False}
            return [f"[bot] REWARD: {enemy.blueprint} {self.pending['how']}; no jump until its reward is in"]
        pending = self.pending
        if snapshot.event_open:
            pending["dialog"] = True
            return []
        if pending["dialog"]:
            after = self._resources(player)
            gained = [f"{after[k] - v:+d} {k}" for k, v in pending["before"].items() if after[k] != v]
            self.pending, self.handled = None, True
            return [f"[bot] REWARD claimed from {pending['enemy']}: {', '.join(gained) or 'nothing countable'}"
                    f" (scrap {pending['before']['scrap']} -> {after['scrap']})"]
        if now - pending["since"] > self.TIMEOUT:
            self.pending, self.handled = None, True
            return [f"[bot] REWARD: no reward dialog {self.TIMEOUT:.0f}s after {pending['enemy']} was "
                    f"{pending['how']}; moving on"]
        return []

    def waiting(self) -> str | None:
        if self.pending is None:
            return None
        return f"waiting for the reward from {self.pending['enemy']} ({self.pending['how']})"


class JumpPilot:
    """Opens the map, picks the next beacon (or sector) and jumps, checking each step."""

    MAX_WAIT_SECONDS = 45.0   # give up waiting for healing/repairs after this long at one beacon
    RETRY_SECONDS = 5.0

    def __init__(self, game: GameInput, proc, live: bool, rewards: RewardWatch | None = None):
        self.game, self.proc, self.live = game, proc, live
        self.location = None
        self.arrived = 0.0
        self.status = None
        self.next_try = 0.0
        self.rewards = rewards

    def _say(self, status: str) -> list[str]:
        if status == self.status:
            return []
        self.status = status
        return [f"[bot] {status}"]

    def _event_value(self, snapshot: GameSnapshot):
        def value(beacon) -> tuple[float, bool]:
            outcome = read_outcome(self.proc, beacon.event) if beacon.event else None
            score = max(-50.0, outcome_value(outcome, snapshot.player, [], snapshot.sector or 1))
            return score, bool(outcome and outcome.environment)
        return value

    def step(self, snapshot: GameSnapshot, now: float) -> list[str]:
        state = read_star_map(self.proc)
        if state.current != self.location:
            self.location, self.arrived = state.current, now
        waiting = self.rewards.waiting() if self.rewards is not None else None
        if waiting:
            return self._say(waiting)
        blockers = jump_blockers(snapshot)
        hard = [b for b in blockers if not b.startswith(("healing", "repairing", "fires"))]
        if hard:
            return []
        if blockers and now - self.arrived < self.MAX_WAIT_SECONDS:
            return self._say(f"waiting before jumping: {'; '.join(blockers)}")
        if now < self.next_try:
            return []
        self.next_try = now + self.RETRY_SECONDS

        if state.choosing_sector:
            return self._pick_sector(state, snapshot)
        if snapshot.player.fuel <= 0:
            return self._no_fuel(state, snapshot)

        plan = choose_destination(state, snapshot, self._event_value(snapshot))
        if plan is None:
            return self._say("no beacon to jump to")
        lines = [f"[bot] JUMP plan: " + ("next sector" if plan.leave_sector else
                 f"beacon at ({plan.beacon.x:.0f},{plan.beacon.y:.0f})")
                 + f" [{plan.score:+.0f}] {', '.join(plan.notes)}"]
        if not self.live:
            self.next_try = now + 30
            return lines + ["[bot] jump: dry run, not jumping"]
        return lines + self._execute(state, snapshot, plan)

    def escape(self, snapshot: GameSnapshot) -> list[str]:
        """Jump away mid-fight (critical hull), to the best beacon the planner finds."""
        state = read_star_map(self.proc)
        plan = choose_destination(state, snapshot, self._event_value(snapshot))
        if plan is None:
            return ["[bot] escape: no beacon to jump to"]
        where = "next sector" if plan.leave_sector else f"beacon at ({plan.beacon.x:.0f},{plan.beacon.y:.0f})"
        lines = [f"[bot] ESCAPE to {where}: {', '.join(plan.notes)}"]
        if not self.live:
            return lines + ["[bot] escape: dry run, not jumping"]
        return lines + self._execute(state, snapshot, plan)

    def _execute(self, state, snapshot: GameSnapshot, plan) -> list[str]:
        lines = []
        if not state.open:
            self.game.press("jump", snapshot)
            time.sleep(0.8)   # let the map finish opening before clicking on it
            state = read_star_map(self.proc)
            if not state.open:
                return lines + ["[bot] jump: the map did not open (FTL drive not ready?)"]

        if state.distress_on and snapshot.player.fuel > 0 and state.distress_button is not None:
            # Fuel again: stop calling for help before jumping on.
            if self._click_map_button("distress", snapshot):
                lines.append("[bot] fuel again: distress beacon off")
            state = read_star_map(self.proc)

        if plan.leave_sector:
            if state.end_button is None:
                return lines + ["[bot] jump: the next-sector button is not available"]
            x, y, w, h = state.end_button
            self.game.click(x + w / 2, y + h / 2, snapshot.mapping)
            time.sleep(0.6)
            state = read_star_map(self.proc)
            if state.choosing_sector:
                return lines + self._pick_sector(state, snapshot)
            return lines + ["[bot] jump: the sector map did not open"]

        x, y = state.screen_pos(plan.beacon)
        self.game.hover(x, y, snapshot.mapping)
        time.sleep(0.1)
        if read_star_map(self.proc).potential != plan.beacon.address:
            close_window(self.game, self.proc, snapshot, "map")
            return lines + ["[bot] jump: the beacon could not be selected under the mouse; closed the map"]
        for attempt in range(2):
            self.game.slow_click(x, y, snapshot.mapping)
            time.sleep(0.6)
            after = read_star_map(self.proc)
            if after.current != state.current or not after.open:
                return lines + [f"[bot] jump: ok, on the way" + (" (second click)" if attempt else "")]
        diagnosis = self._diagnose(plan.beacon.address)
        close_window(self.game, self.proc, snapshot, "map")
        return lines + [f"[bot] jump: two clicks did not start a jump; closed the map and will retry",
                        f"[bot] jump diagnosis: {diagnosis}"]

    def _click_map_button(self, which: str, snapshot: GameSnapshot) -> bool:
        """Click the star map's Wait or Distress button, checking the game sees it hovered first."""
        offset = {"wait": L.StarMap.WAIT_BUTTON, "distress": L.StarMap.DISTRESS_BUTTON}[which]
        state = read_star_map(self.proc)
        rect = state.wait_button if which == "wait" else state.distress_button
        if rect is None:
            return False
        x, y, w, h = rect
        self.game.hover(x + w / 2, y + h / 2, snapshot.mapping)
        time.sleep(0.08)
        world = self.proc.ptr(self.proc.ptr(L.P_THE_APP) + L.CApp.WORLD)
        if not self.proc.bool(world + L.WorldManager.STAR_MAP + offset + L.GenericButton.HOVER):
            return False
        self.game.slow_click(x + w / 2, y + h / 2, snapshot.mapping)
        time.sleep(0.4)
        return True

    def _no_fuel(self, state, snapshot: GameSnapshot) -> list[str]:
        """Out of fuel no beacon can be chosen. Turn on the distress beacon and wait: passing
        ships may sell or give fuel (or attack). Each wait lets the rebel fleet advance."""
        if not self.live:
            self.next_try = time.monotonic() + 30
            return ["[bot] no fuel: would turn on the distress beacon and wait"]
        lines = []
        if not state.open:
            self.game.press("jump", snapshot)
            time.sleep(0.8)
            state = read_star_map(self.proc)
            if not state.open:
                return ["[bot] no fuel: the map did not open"]
        if not state.distress_on and state.distress_button is not None:
            if self._click_map_button("distress", snapshot) and read_star_map(self.proc).distress_on:
                lines.append("[bot] no fuel: distress beacon on")
            else:
                lines.append("[bot] no fuel: could not turn on the distress beacon")
        if self._click_map_button("wait", snapshot):
            after = read_star_map(self.proc)
            return lines + ["[bot] no fuel: waiting for a passing ship" + ("" if not after.open else " (map still open)")]
        close_window(self.game, self.proc, snapshot, "map")
        return lines + ["[bot] no fuel: the Wait button was not found; closed the map"]

    def _diagnose(self, target: int) -> str:
        """What StarMap::MouseClick would have seen: its buttons are checked before travel."""
        world = self.proc.ptr(self.proc.ptr(L.P_THE_APP) + L.CApp.WORLD)
        view = self.proc.view(world + L.WorldManager.STAR_MAP, L.StarMap.SIZE)
        buttons = {"close": 0x678, "wait": 0x1D8, "distress": 0x2D8, "next sector": 0xD8}
        hovered = [name for name, offset in buttons.items()
                   if view.bool(offset + L.GenericButton.ACTIVE) and view.bool(offset + L.GenericButton.HOVER)]
        mouse = self.proc.view(L.MOUSE_CONTROL, 8).pair_i(0)
        return (f"target selected={view.ptr(L.StarMap.POTENTIAL_LOC) == target}, "
                f"hovered={view.ptr(L.StarMap.HOVER_LOC) == target}, ready_to_travel={view.bool(L.StarMap.READY_TO_TRAVEL)}, "
                f"buttons under mouse={hovered or 'none'}, mouse={mouse}, flag 0x83c={view.bool(0x83C)}")

    def _pick_sector(self, state, snapshot: GameSnapshot) -> list[str]:
        index = choose_sector(state, snapshot)
        if index is None:
            return ["[bot] jump: no sector choices visible"]
        option = state.sector_options[index]
        listing = ", ".join(f"{o.kind or '?'}" for o in state.sector_options)
        x, y, w, h = option.rect
        self.game.click(x + w / 2, y + h / 2, snapshot.mapping)
        time.sleep(0.8)
        after = read_star_map(self.proc)
        moved_on = not after.choosing_sector or after.sector != state.sector
        return [f"[bot] next sector: picked {option.kind or option.name} from [{listing}] -> "
                f"{'ok' if moved_on else 'the sector map did not change'}"]


DESELECT_SPOT = (1000, 25)                 # empty screen area (game coords): a left click there drops the crew selection


def toggle_door(intent: Intent, game: GameInput, proc, snapshot: GameSnapshot, live: bool) -> tuple[bool, str | None]:
    """Open or close one door, checked at every step. FTL only lets the mouse pick a door
    (CrewControl.selectedDoor) while no crew member is selected, so a selection left by a
    crew order is dropped first; then a few points around the door are tried until the game
    reports this door under the mouse, and only then is it clicked (a blast door can take two
    clicks: the first selects it, the second works it)."""
    door = intent.door
    if live and proc.bool(door.address + L.Door.OPEN) == intent.want_open:
        return True, "already " + ("open" if intent.want_open else "closed")
    ox, oy = snapshot.player_origin
    if not live:
        game.slow_click(ox + door.x, oy + door.y, snapshot.mapping)
        return True, None
    gui = proc.ptr(proc.ptr(L.P_THE_APP) + L.CApp.GUI)
    crew_control = gui + L.CommandGui.CREW_CONTROL
    if read_snapshot(proc).selected_crew:
        game.click(*DESELECT_SPOT, snapshot.mapping)
        time.sleep(0.1)
    spot = None
    for dx, dy in ((0, 0), (0, -5), (0, 5), (-5, 0), (5, 0), (0, -9), (0, 9), (-9, 0), (9, 0)):
        game.hover(ox + door.x + dx, oy + door.y + dy, snapshot.mapping)
        time.sleep(0.05)
        if proc.ptr(crew_control + L.CrewControl.SELECTED_DOOR) == door.address:
            spot = (ox + door.x + dx, oy + door.y + dy)
            break
    if spot is None:
        selected = bool(read_snapshot(proc).selected_crew)
        return False, "the game did not show the door under the mouse" + (" (a crew member is still selected)" if selected else "")
    for attempt in range(2):
        game.slow_click(*spot, snapshot.mapping)
        time.sleep(0.2)
        now_open = proc.bool(door.address + L.Door.OPEN)
        if now_open == intent.want_open:
            return True, "ok: " + ("opened" if now_open else "closed") + (" (second click)" if attempt else "")
    return False, "the door did not move after two clicks"


def drag_beam(game: GameInput, snapshot: GameSnapshot, room, start: tuple[float, float]):
    """A beam is aimed by pressing in the target room and dragging: the beam runs from the
    press point toward the release point. Drag toward the middle of the enemy ship so it
    crosses more rooms."""
    ox, oy = snapshot.enemy_origin
    rooms = snapshot.enemy.rooms
    cx = sum(r.x + r.w / 2 for r in rooms) / max(1, len(rooms))
    cy = sum(r.y + r.h / 2 for r in rooms) / max(1, len(rooms))
    dx, dy = cx - start[0], cy - start[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length < 20:                       # the target is the middle: sweep across it instead
        dx, dy, length = 1.0, 0.0, 1.0
    end = (start[0] + dx / length * BEAM_DRAG, start[1] + dy / length * BEAM_DRAG)
    game.hover(ox + start[0], oy + start[1], snapshot.mapping)
    game._xdotool("mousedown", "1")
    for step in range(1, 5):              # move in steps so the game sees the drag
        t = step / 4
        game.hover(ox + start[0] + (end[0] - start[0]) * t, oy + start[1] + (end[1] - start[1]) * t,
                   snapshot.mapping)
    game._xdotool("mouseup", "1")


BEAM_DRAG = 90                            # pixels (about 2.5 tiles) from the press point
SYSTEM_OF_KEY = {"start_hacking": "hacking", "send_tele": "teleporter", "ret_tele": "teleporter", "mind": "mind"}


def system_target(intent: Intent, game: GameInput, proc, snapshot: GameSnapshot, live: bool) -> tuple[bool, str | None]:
    """Arm a system with its hotkey (hacking probe, teleporter, mind control), then click the
    target room: on the enemy ship checked through CombatControl.selectedRoom like weapon aiming,
    on ours at the room's centre. Success = the system's state changed."""
    name = SYSTEM_OF_KEY[intent.system]
    before = snapshot.player.systems[name].special if name in snapshot.player.systems else {}
    if not game.press(intent.system, snapshot):
        return False, "no hotkey for it"
    room = intent.room
    if intent.on_enemy:
        if snapshot.enemy_origin is None:
            game.cancel_aim()
            return False, "no enemy on screen"
        ox, oy = snapshot.enemy_origin
        spots = [(room.x + room.w / 2, room.y + room.h / 2)]
        spots += [(room.x + tx + 17, room.y + ty + 17) for ty in range(0, room.h, 35) for tx in range(0, room.w, 35)]
        if not live:
            game.slow_click(ox + spots[0][0], oy + spots[0][1], snapshot.mapping)
            return True, None
        gui = proc.ptr(proc.ptr(L.P_THE_APP) + L.CApp.GUI)
        selected = gui + L.CommandGui.COMBAT_CONTROL + L.CombatControl.SELECTED_ROOM
        for x, y in spots:
            game.hover(ox + x, oy + y, snapshot.mapping)
            time.sleep(0.05)
            if proc.i32(selected) == room.room_id:
                game.slow_click(ox + x, oy + y, snapshot.mapping)
                break
        else:
            game.cancel_aim()
            return False, f"room {room.room_id} not found under the mouse; cancelled"
    else:
        ox, oy = snapshot.player_origin
        game.slow_click(ox + room.x + room.w / 2, oy + room.y + room.h / 2, snapshot.mapping)
    if not live:
        return True, None
    time.sleep(0.3)
    after = read_snapshot(proc).player.systems.get(name)
    keys = {"hacking": ("targeted",), "teleporter": ("can_send",), "mind": ("can_use", "controlled")}[name]
    if after is not None and any(after.special.get(k) != before.get(k) for k in keys):
        return True, "ok"
    game.cancel_aim()
    return False, "nothing changed (not ready?); cancelled"


def aim_weapon(intent: Intent, game: GameInput, proc, snapshot: GameSnapshot, live: bool) -> tuple[bool, str | None]:
    """Arm the weapon, find a spot in the target room that the game registers as that room
    (CombatControl.selectedRoom), then click it."""
    if snapshot.enemy_origin is None or intent.weapon_index > 3:
        return False, "no enemy on screen"
    if not game.press(f"weapon{intent.weapon_index + 1}", snapshot):
        return False, "no hotkey for that weapon"
    room = intent.room
    ox, oy = snapshot.enemy_origin
    spots = [(room.x + room.w / 2, room.y + room.h / 2)]
    spots += [(room.x + tx + 17, room.y + ty + 17) for ty in range(0, room.h, 35) for tx in range(0, room.w, 35)]
    if not live:
        game.slow_click(ox + spots[0][0], oy + spots[0][1], snapshot.mapping)
        return True, None
    time.sleep(0.05)
    gui = proc.ptr(proc.ptr(L.P_THE_APP) + L.CApp.GUI)
    selected_room = gui + L.CommandGui.COMBAT_CONTROL + L.CombatControl.SELECTED_ROOM
    seen = None
    for x, y in spots:
        game.hover(ox + x, oy + y, snapshot.mapping)
        time.sleep(0.05)
        seen = proc.i32(selected_room)
        if seen == room.room_id:
            weapon = snapshot.player.weapons[intent.weapon_index]
            if weapon.weapon_type == "BEAM":
                drag_beam(game, snapshot, room, (x, y))
            else:
                game.slow_click(ox + x, oy + y, snapshot.mapping)
            return True, None
    game.cancel_aim()
    return False, f"room {room.room_id} not found under the mouse (the game saw room {seen}); cancelled"


def execute(intent: Intent, game: GameInput, proc, snapshot: GameSnapshot, live: bool) -> tuple[bool, str | None]:
    if intent.kind == "crew_to_room":
        return send_crew(intent, game, proc, snapshot, live)
    if intent.kind == "door":
        return toggle_door(intent, game, proc, snapshot, live)
    if intent.kind == "doors_all":
        # FTL's Open All Doors / Close All Doors hotkeys (default Z / X).
        done = game.press("open" if intent.want_open else "close", snapshot)
        if done and live:
            time.sleep(0.15)
            doors = read_snapshot(proc).player.doors
            moved = any(d.open == intent.want_open for d in doors) if intent.want_open else not any(
                d.open and d.airlock for d in doors)
            return moved, ("ok" if moved else "the doors did not move")
        return done, None
    if intent.kind == "target":
        before = snapshot.player.weapons[intent.weapon_index] if intent.weapon_index < len(snapshot.player.weapons) else None
        done, note = aim_weapon(intent, game, proc, snapshot, live)
        if not (done and live):
            return done, note
        time.sleep(0.12)
        if weapon_armed(proc):
            game.cancel_aim()
            return False, "a weapon was left armed; cancelled it"
        # ProjectileFactory::Fire stores the target (targets, fireWhenReady) even while the
        # weapon charges; no target and no shot means the click did not register.
        weapons = read_snapshot(proc).player.weapons
        after = weapons[intent.weapon_index] if intent.weapon_index < len(weapons) else None
        fired = before is not None and after is not None and after.charge < before.charge
        if after is not None and not after.targeted and not fired:
            return False, "aim did not stick (the weapon has no target)"
        return True, None
    if intent.kind == "system_target":
        return system_target(intent, game, proc, snapshot, live)
    if intent.kind == "system_key":
        return game.press(intent.system, snapshot), None
    if intent.kind == "autofire":
        # The autofire key toggles every weapon's autoFiring (ProjectileFactory.autoFiring).
        done = game.press("autofire", snapshot)
        if done and live:
            time.sleep(0.15)
            weapons = read_snapshot(proc).player.weapons
            now_on = any(w.auto_firing for w in weapons)
            return now_on == intent.want_open, ("ok" if now_on == intent.want_open else "did not switch")
        return done, None
    if intent.kind == "lockdown":
        # Select the Crystal crew member, then their Lockdown ability (hotkey, default P).
        if not game.select_crew(intent.crew, snapshot):
            return False, "could not select them"
        time.sleep(0.1)
        return game.press("lockdown", snapshot), None
    if intent.kind == "cloak":
        return game.press("activate_cloak", snapshot), None
    if intent.kind == "unpower_drone":
        done = game.press(f"drone{intent.weapon_index + 1}", snapshot, shift=True)   # Shift+key powers it down
        if done and live:
            time.sleep(0.15)
            drones = read_snapshot(proc).player.drones
            off = intent.weapon_index < len(drones) and not drones[intent.weapon_index].powered
            return off, ("ok: stood down" if off else "still powered")
        return done, None
    if intent.kind == "power_drone":
        done = game.press(f"drone{intent.weapon_index + 1}", snapshot)
        if done and live:
            time.sleep(0.15)
            drones = read_snapshot(proc).player.drones
            powered = intent.weapon_index < len(drones) and drones[intent.weapon_index].powered
            return powered, ("ok: launched" if powered else "did not launch")
        return done, None
    if intent.kind == "power_weapon":
        done = game.press(f"weapon{intent.weapon_index + 1}", snapshot)
    elif intent.kind in ("power_system", "depower_system"):
        # A system hotkey adds a bar; with Shift held it removes one (SystemControl::OnKeyDown).
        before = snapshot.player.systems.get(intent.system)
        add = intent.kind == "power_system"
        done = game.press(intent.system, snapshot, shift=not add)
        if done and live and before is not None:
            time.sleep(0.12)
            after = read_snapshot(proc).player.systems.get(intent.system)
            changed = after is not None and (after.power > before.power if add else after.power < before.power)
            return changed, (f"ok: {after.power} bars" if changed else "power did not change")
        return done, None
    elif intent.kind == "pause":
        done = game.press("pause", snapshot)
    else:
        return intent.kind == "alert", None
    if not (done and live):
        return done, None

    time.sleep(0.12)
    # Never leave a weapon armed: FTL can crash if one dangles across a game load.
    if intent.kind in ("power_weapon", "target") and weapon_armed(proc):
        game.cancel_aim()
        return done, "a weapon was left armed; cancelled it"
    if intent.kind == "power_weapon":
        weapon = read_snapshot(proc).player.weapons[intent.weapon_index]
        return weapon.powered, ("ok: powered" if weapon.powered else "did not power up")
    return done, None


class Tee:
    """Write the bot's output to the terminal and, time-stamped, to data/logs/bot.log
    (which ftl-dash shows)."""

    def __init__(self, terminal, log_path, on_line=None):
        self.terminal = terminal
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = open(log_path, "a", buffering=1, encoding="utf-8")
        self.at_line_start = True
        self.on_line = on_line             # gets every complete line (the run recorder)
        self.partial = ""

    def write(self, text):
        self.terminal.write(text)
        stamped = []
        for piece in text.splitlines(keepends=True):
            if self.at_line_start and piece.strip():
                stamped.append(time.strftime("%H:%M:%S ") + piece)
            else:
                stamped.append(piece)
            self.at_line_start = piece.endswith("\n")
            if self.on_line is not None:
                self.partial += piece
                if self.at_line_start:
                    self.on_line(self.partial)
                    self.partial = ""
        self.log.write("".join(stamped))
        return len(text)

    def flush(self):
        self.terminal.flush()
        self.log.flush()


def main():
    recorder = RunRecorder()
    sys.stdout = Tee(sys.stdout, LOG_DIR / "bot.log", on_line=recorder.bot_line)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="send input to FTL (default: dry run)")
    parser.add_argument("--no-volley", action="store_true", help="fire each gun as soon as it is charged")
    parser.add_argument("--no-events", action="store_true", help="leave event dialogs to you")
    parser.add_argument("--no-jump", action="store_true", help="leave jumping and sector choice to you")
    parser.add_argument("--no-shop", action="store_true", help="leave stores to you")
    parser.add_argument("--no-upgrade", action="store_true", help="leave ship upgrades to you")
    parser.add_argument("--hz", type=float, default=5.0, help="decisions per second (default 5)")
    parser.add_argument("--no-dash", action="store_true", help="do not start the browser dashboard")
    args = parser.parse_args()

    try:
        ensure_host()
        proc = HostClient(SOCKET_PATH)
    except AttachError as error:
        print(f"[bot] {error}")
        sys.exit(1)

    # Focus is checked with the X server (GameInput.ftl_is_active), not FTL's own flag.
    game = GameInput(proc.pid, FTL_SAVE_DIR / "settings.ini", dry_run=not args.live)
    brain = LiveCombatBrain(volley=not args.no_volley)
    events = EventPilot(game, proc, args.live)
    rewards = RewardWatch()
    jumps = JumpPilot(game, proc, args.live, rewards)
    shops = ShopPilot(game, proc, args.live)
    upgrades = UpgradePilot(game, proc, args.live)
    equip = EquipmentPilot(game, proc, args.live)
    restarter = RestartPilot(game, proc, args.live)
    mode = "LIVE" if args.live else "DRY RUN"
    print(f"[bot] {mode}: connected to FTL PID {proc.pid}. Ctrl-C to stop.")
    if game.nested:
        print(f"[bot] FTL runs in its own window (display {game.display}): the bot plays there without "
              f"touching your mouse or other windows")
    if not args.no_dash:
        was_up = dashboard_is_up()
        if ensure_dashboard():
            print(f"[bot] dashboard: {DASHBOARD_URL}")
            if not was_up:
                webbrowser.open(DASHBOARD_URL)   # only when it was just started, not on every restart
        else:
            print("[bot] dashboard did not start; see data/logs/dashboard.log")
        if not ensure_overlay(game.display):
            print("[bot] the overlay did not start; see data/logs/overlay.log")

    last_advice = None
    last_combat = None
    last_resume = 0.0
    last_settings = 0.0
    last_window = 0.0
    last_augments = None
    foreign_screen_since = None
    play_status = "active"
    direct_ok = args.live and game.background_keys_work()
    failed_resumes = 0
    warned_direct = False
    settings = bot_settings.load()
    speed_problem = None
    slowed_for_boss = False
    try:
        while proc.alive():
            now = time.monotonic()
            try:
                snapshot = read_snapshot(proc)
                if args.live:
                    if snapshot.game_over and not recorder.ftl_reason:
                        recorder.ftl_reason = game_over_reason(proc)
                    recorder.observe(snapshot, play_status)
            except MemoryReadError as error:
                print(f"[bot] read failed: {error}")
                time.sleep(1)
                continue

            # A run ended (or FTL sits at the menu): start the next one by itself.
            if args.live and (snapshot.game_over or snapshot.screen == "main_menu"):
                if now - last_settings > 1.0:
                    settings, last_settings = bot_settings.load(), now
                if settings["autoplay"]:
                    try:
                        for line in restarter.step(snapshot, now):
                            print(line)
                    except FocusLost:
                        pass
                    except (MemoryReadError, AttributeError, IndexError) as error:
                        print(f"[bot] restart failed: {error}")
                    game.drain_log()
                if snapshot.game_over or snapshot.screen != "in_run":
                    time.sleep(max(0.0, 1.0 / args.hz - (time.monotonic() - now)))
                    continue

            if snapshot.screen == "in_run" and snapshot.player is not None:
                if upgrades.profile is None or upgrades.profile.blueprint != snapshot.player.blueprint:
                    upgrades.profile = profile_for(snapshot.player.blueprint)
                    pf = upgrades.profile
                    # adapt_upgrade_steps fills pf.notes with what it changes for this ship.
                    adapt_upgrade_steps([("shields", 4, "")], pf, {
                        name: system.level for name, system in snapshot.player.systems.items()}, snapshot.player)
                    print(f"[bot] ship {pf.blueprint}: {pf.runs} past runs, {pf.wins} won, avg sector {pf.sector_avg:.1f}"
                          + (f"; adapting: {'; '.join(pf.notes)}" if pf.notes else ""))
                augments = dict(snapshot.player.augments)
                if augments != last_augments:
                    rules = describe_augments(snapshot.player)
                    print(f"[bot] augments: {', '.join(augments) or 'none'}"
                          + (f"; adapting: {'; '.join(rules)}" if rules else ""))
                    last_augments = augments
                combat = in_combat(snapshot)
                if combat != last_combat:
                    print(f"[bot] {'COMBAT with ' + snapshot.enemy.blueprint if combat else 'no combat'}")
                    last_combat = combat

                if not combat:
                    advice = decide_next_action(to_legacy_game_state(snapshot))
                    if advice.name != last_advice:
                        print(f"[bot] advice: {advice.name} - {advice.reason}")
                        last_advice = advice.name

                # The switches (data/bot_settings.json): F9/F10/F11 in the game, or the dashboard.
                if now - last_settings > 1.0:
                    settings = bot_settings.load()
                    autoplay = settings["autoplay"] and args.live
                    # Auto-play sends every click and key straight to FTL's window, so it never
                    # moves your mouse and keeps playing while FTL is behind other windows.
                    # (Not needed when FTL has its own Xephyr window: there it always has focus.)
                    game.direct_mode = autoplay and direct_ok and not game.nested
                    if autoplay and not direct_ok and not game.nested and not warned_direct:
                        print("[bot] auto-play cannot send keys to this FTL while it is behind other windows "
                              "(it was started before that worked): Save and Quit FTL, then start the bot again. "
                              "Until then it only plays while FTL is the active window.")
                        warned_direct = True
                    if args.live:      # also re-applies the speed after FTL restarts
                        # The Flagship is fought at normal speed: every second of reaction counts.
                        boss_fight = bool(last_combat) and is_flagship(snapshot.enemy)
                        if boss_fight != slowed_for_boss and settings["speed"] > 0:
                            print("[bot] Flagship fight: game speed back to normal until it is over" if boss_fight
                                  else "[bot] Flagship fight over: game speed restored")
                        slowed_for_boss = boss_fight
                        problem = bot_settings.apply_speed(proc, 0 if boss_fight else settings["speed"])
                        if problem and problem != speed_problem:
                            print(f"[bot] speed: {problem}")
                        speed_problem = problem
                    last_settings = now

                active = not args.live or game.ftl_is_active()
                if not args.live:
                    status = "active"
                elif not settings["autoplay"]:
                    status = "manual"            # the player is in control; the bot only watches
                elif game.direct_mode:
                    status = "active" if active else "background"
                else:
                    status = "active" if active else "waiting"
                may_act = status in ("active", "background")
                if status != play_status:
                    print({"active": "[bot] auto-play: playing (FTL in front)",
                           "background": "[bot] auto-play: playing with FTL in the background",
                           "manual": "[bot] manual control: auto-play is off (F9 in the game turns it on)",
                           "waiting": "[bot] auto-play is waiting: FTL is not the active window"}[status])
                    play_status = status
                # FTL pauses itself when it loses focus (fullscreen, or the altpause option) and
                # does not always resume; that pause also blocks event dialogs. Resume it when FTL
                # is in front again, and while it is behind other windows, where nobody but the
                # bot can have paused it. The pause menu is the player's: leave it alone.
                focus_pause = not snapshot.menu_open and (snapshot.focus_paused or (status == "background"
                                                                                    and snapshot.pause_flag))
                if focus_pause and may_act and now - last_resume > (1.0 if status == "background" else 5.0):
                    try:
                        game.press("pause", snapshot)
                        time.sleep(0.15)
                        if read_snapshot(proc).pause_flag:
                            failed_resumes += 1
                        else:
                            failed_resumes = 0
                            print("[bot] resumed the game (FTL had paused itself when it lost focus)")
                    except FocusLost:
                        pass
                    last_resume = now
                    game.drain_log()
                    if failed_resumes >= 3 and status == "background":
                        # Keys sent to FTL's window are not arriving: stop pressing every second.
                        print("[bot] auto-play: FTL ignores the keys sent to its window; playing only while "
                              "FTL is the active window")
                        direct_ok, failed_resumes = False, 0
                        game.direct_mode = False
                # A ship screen the bot did not open (an event's over-capacity item, for one) blocks
                # everything in auto-play: close it after a moment; the extra item stays behind.
                if snapshot.ship_screen_open and may_act and not upgrades.visiting and not equip.visiting:
                    foreign_screen_since = foreign_screen_since or now
                    if equip.pending(snapshot):
                        # A reward that does not fit: keep the best items, then close.
                        try:
                            for line in equip.step(snapshot):
                                print(line)
                        except FocusLost:
                            pass
                        except (MemoryReadError, AttributeError, IndexError, KeyError, TypeError, StopIteration) as error:
                            print(f"[bot] equipment failed: {error}")
                            equip.retry_after = time.monotonic() + 60
                        game.drain_log()
                    elif now - foreign_screen_since > 4.0:
                        try:
                            close_window(game, proc, snapshot, "ship_screen")
                            print("[bot] closed a ship screen an event left open (an item over capacity stays behind)")
                        except FocusLost:
                            pass
                        game.drain_log()
                        foreign_screen_since = None
                else:
                    foreign_screen_since = None
                # Safety net: if one of our Escapes opened the pause menu, close it again.
                if snapshot.menu_open and args.live and may_act and now - game.last_escape < 3.0:
                    try:
                        game.press_key("Escape", snapshot)
                        game.last_escape = 0.0
                        print("[bot] closed the pause menu that an Escape of mine opened")
                    except FocusLost:
                        pass
                    game.drain_log()
                    time.sleep(0.3)
                    continue
                if snapshot.event_open and not args.no_events and may_act:
                    try:
                        for line in events.step(snapshot, now):
                            print(line)
                    except FocusLost:
                        print("[bot] FTL lost focus; stopped choosing the event")
                    except (MemoryReadError, AttributeError, IndexError) as error:
                        print(f"[bot] could not read the event: {error}")
                    game.drain_log()

                intents = brain.decide(snapshot, now)
                for line in rewards.observe(snapshot, now):
                    print(line)
                # FTL ignores keys for a moment while a dialog or fight winds down: wait before
                # opening the store or the upgrade screen.
                if snapshot.event_open or snapshot.map_open or snapshot.menu_open or in_combat(snapshot):
                    last_window = now
                calm = (not intents and may_act and not in_combat(snapshot) and not snapshot.event_open
                        and not snapshot.map_open and not snapshot.menu_open and now - last_window > 2.5
                        and rewards.waiting() is None)     # no windows while a wreck's reward is due
                shopping = not args.no_shop and shops.pending(snapshot)
                if calm and shopping:
                    try:
                        for line in shops.step(snapshot):
                            print(line)
                    except FocusLost:
                        print("[bot] FTL lost focus; stopped shopping")
                    except (MemoryReadError, AttributeError, IndexError, KeyError, TypeError) as error:
                        print(f"[bot] shopping failed: {error}")
                    game.drain_log()
                elif calm and equip.pending(snapshot):
                    try:
                        for line in equip.step(snapshot):
                            print(line)
                    except FocusLost:
                        print("[bot] FTL lost focus; stopped rearranging the weapons")
                    except (MemoryReadError, AttributeError, IndexError, KeyError, TypeError, StopIteration) as error:
                        print(f"[bot] equipment failed: {error}")
                        equip.retry_after = time.monotonic() + 60
                    game.drain_log()
                elif calm and not args.no_upgrade and upgrades.pending(snapshot):
                    try:
                        for line in upgrades.step(snapshot):
                            print(line)
                    except FocusLost:
                        print("[bot] FTL lost focus; stopped upgrading (will finish when FTL is back)")
                        upgrades.interrupted()
                    except (MemoryReadError, AttributeError, IndexError, KeyError, TypeError) as error:
                        print(f"[bot] upgrading failed: {error}")
                    game.drain_log()
                elif not intents and not args.no_jump and may_act and not shopping:
                    try:
                        for line in jumps.step(snapshot, now):
                            print(line)
                    except FocusLost:
                        print("[bot] FTL lost focus; stopped the jump")
                    except (MemoryReadError, AttributeError, IndexError, KeyError) as error:
                        print(f"[bot] could not plan the jump: {error}")
                    game.drain_log()

                for intent in intents:
                    if intent.kind == "escape" and may_act:
                        print(f"[bot] {intent.reason}")
                        try:
                            for line in jumps.escape(snapshot):
                                print(line)
                        except FocusLost:
                            print("[bot] FTL lost focus; stopped the escape")
                        except (MemoryReadError, AttributeError, IndexError, KeyError) as error:
                            print(f"[bot] escape failed: {error}")
                        game.drain_log()
                        continue
                    if not may_act:
                        if intent.crew is not None:
                            brain.forget(intent.crew.address)
                        continue
                    try:
                        done, result = execute(intent, game, proc, snapshot, args.live)
                    except FocusLost:
                        done, result = False, "FTL lost focus; stopped"
                    except (MemoryReadError, AttributeError, IndexError) as error:
                        done, result = False, f"could not check result: {error}"
                    if not done and intent.crew is not None:
                        brain.forget(intent.crew.address)
                    if not done and intent.door is not None and args.live:
                        brain.door_failed(intent.door.address)
                    print(f"[bot] {intent.kind}: {intent.reason}"
                          + (f" -> {result}" if result else ("" if done else " [not executed]")))
                    if not args.live:
                        for line in game.drain_log():
                            print(f"        would run: {line}")
                    game.drain_log()

            time.sleep(max(0.0, 1.0 / args.hz - (time.monotonic() - now)))
        print("[bot] FTL exited.")
        recorder.close("FTL exited")
    except KeyboardInterrupt:
        print("\n[bot] Stopped.")
        recorder.close("bot stopped (Ctrl-C)")


if __name__ == "__main__":
    main()
