"""Record every run the bot plays, for post-mortems (read by Claude; see data/runs/README.md).

One directory per run under data/runs/:
    events.jsonl   one JSON object per line, in time order: what happened (state changes the
                   recorder detects) and every line the bot printed (kind "bot")
    samples.jsonl  the ship's state every SAMPLE_SECONDS
    summary.json   written when the run ends (and refreshed every SUMMARY_SECONDS meanwhile):
                   outcome, cause, what went well and badly, the last events before the end
data/runs/index.jsonl gets one line per finished run. postmortem.py compiles all failed runs.

The recorder only reads snapshots; any error inside it is swallowed so it can never stop
the bot.
"""

import json
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path

from config import PROJECT_DIR
from observer.memory.adapter import in_combat

RUNS_DIR = PROJECT_DIR / "data" / "runs"
CURRENT_FILE = RUNS_DIR / "current.json"
INDEX_FILE = RUNS_DIR / "index.jsonl"
SAMPLE_SECONDS = 3.0
SUMMARY_SECONDS = 60.0
TAIL_SECONDS = 120.0
BAD_LINE_WORDS = ("failed", "did not", "not found", "could not", "not executed", "stuck", "did not take",
                  "left armed", "lost focus")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _crew_list(ship) -> list:
    return [c for c in ship.crew if c.room_id >= 0 and "drone" not in f"{c.name} {c.species}".lower()]


class RunRecorder:
    def __init__(self):
        self.run_dir: Path | None = None
        self.run: dict = {}
        self.events = None
        self.samples = None
        self.prev = None                   # previous snapshot (in a run)
        self.prev_crew: dict = {}          # address -> CrewInfo (own crew, previous tick)
        self.last_sample = 0.0
        self.last_summary = 0.0
        self.tail: deque = deque(maxlen=4000)
        self.stats: dict = {}
        self.fight: dict | None = None
        self.bot_status = "active"
        self.ftl_reason = ""               # FTL's game over text id, set by the bot (gameover_blowup, ...)
        self.status_since = time.monotonic()

    # --- public ---------------------------------------------------------------

    def bot_line(self, text: str):
        """Every line the bot prints (hooked into bot.Tee)."""
        try:
            text = text.strip()
            if text and self.run_dir is not None:
                self._event("bot", text=text)
                if any(word in text for word in BAD_LINE_WORDS):
                    self.stats["failed_actions"] = self.stats.get("failed_actions", 0) + 1
                    failures = self.stats.setdefault("failure_examples", [])
                    if len(failures) < 30:
                        failures.append(text[:200])
                if self.fight is not None:
                    if "target:" in text and "->" not in text.split("target:", 1)[1].split("(room", 1)[-1]:
                        self.fight["aims"] += 1
                    if any(word in text for word in BAD_LINE_WORDS):
                        self.fight["failed_actions"] += 1
                for word, key in (("store: bought", "purchases"), ("upgrade: ", "upgrade_lines"),
                                  ("target:", "aims"), ("door", "door_actions")):
                    if word in text:
                        self.stats[key] = self.stats.get(key, 0) + 1
        except Exception:
            pass

    def observe(self, snapshot, status: str):
        """Called every bot tick with the fresh snapshot and the bot's status
        ("active", "background", "waiting")."""
        try:
            self._observe(snapshot, status)
        except Exception as error:
            self._event("recorder_error", error=repr(error), where=traceback.format_exc(limit=2)[-300:])

    def close(self, reason: str):
        """The bot is stopping: the run may continue later (it is resumed by fingerprint)."""
        try:
            if self.run_dir is not None:
                self._event("bot_stopped", reason=reason)
                self._write_summary(final=False)
        except Exception:
            pass

    # --- internals ---------------------------------------------------------------

    def _event(self, kind: str, **data):
        if self.events is None:
            return
        record = {"t": _now_iso(), "run_s": round(time.monotonic() - self.run.get("_t0", time.monotonic()), 1),
                  "kind": kind, **data}
        self.events.write(json.dumps(record, default=str) + "\n")
        self.events.flush()
        self.tail.append(record)

    def _fingerprint(self, snapshot) -> dict:
        player = snapshot.player
        return {"blueprint": player.blueprint, "ship_name": player.name,
                "crew": sorted(c.name for c in _crew_list(player))}

    def _start_run(self, snapshot):
        player = snapshot.player
        fingerprint = self._fingerprint(snapshot)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        resumed = None
        try:
            current = json.loads(CURRENT_FILE.read_text())
            same_ship = (current.get("blueprint") == fingerprint["blueprint"]
                         and current.get("ship_name") == fingerprint["ship_name"])
            crew_overlap = set(current.get("crew", [])) & set(fingerprint["crew"])
            if same_ship and (crew_overlap or not fingerprint["crew"]) and (snapshot.sector or 0) >= current.get("sector", 0):
                resumed = current
        except (OSError, ValueError):
            pass
        if resumed and Path(resumed["dir"]).exists():
            self.run_dir = Path(resumed["dir"])
            self.run = json.loads((self.run_dir / "run.json").read_text())
            self.stats = self.run.get("stats", {})
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.run_dir = RUNS_DIR / f"{stamp}_{player.blueprint}"
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.run = {"id": self.run_dir.name, "started": _now_iso(), "blueprint": player.blueprint,
                        "ship_name": player.name, "start_sector": snapshot.sector,
                        "attached_mid_run": not (snapshot.sector == 1 and player.scrap <= 30),
                        "start_crew": [(c.name, c.species) for c in _crew_list(player)],
                        "start_weapons": [w.blueprint for w in player.weapons],
                        "start_drones": [d.blueprint for d in player.drones],
                        "start_systems": {n: s.level for n, s in player.systems.items()}}
            self.stats = {}
        self.run["_t0"] = time.monotonic()
        self.events = open(self.run_dir / "events.jsonl", "a", encoding="utf-8")
        self.samples = open(self.run_dir / "samples.jsonl", "a", encoding="utf-8")
        self._event("run_resumed" if resumed else "run_start", **{k: v for k, v in self.run.items() if not k.startswith("_")})
        self._save_current(snapshot)

    def _save_current(self, snapshot):
        data = {**self._fingerprint(snapshot), "sector": snapshot.sector or 0, "dir": str(self.run_dir)}
        CURRENT_FILE.write_text(json.dumps(data))
        self.run["stats"] = self.stats
        (self.run_dir / "run.json").write_text(json.dumps({k: v for k, v in self.run.items() if not k.startswith("_")},
                                                          indent=1, default=str))

    def _end_run(self, outcome: str, cause: str, snapshot):
        if self.fight is not None:
            self._close_fight("we_lost" if outcome in ("destroyed", "crew_lost") else "unfinished", snapshot.player.hull)
        self._event("run_end", outcome=outcome, cause=cause)
        self.run["outcome"], self.run["cause"], self.run["ended"] = outcome, cause, _now_iso()
        summary = self._write_summary(final=True, snapshot=snapshot)
        with open(INDEX_FILE, "a", encoding="utf-8") as index:
            index.write(json.dumps({"id": self.run["id"], "ended": self.run["ended"], "blueprint": self.run["blueprint"],
                                    "outcome": outcome, "cause": cause, "sector": summary.get("sector_reached"),
                                    "dir": str(self.run_dir)}) + "\n")
        CURRENT_FILE.unlink(missing_ok=True)
        for handle in (self.events, self.samples):
            handle.close()
        self.events = self.samples = None
        self.run_dir, self.prev, self.prev_crew, self.fight = None, None, {}, None

    def _observe(self, snapshot, status: str):
        now = time.monotonic()
        in_run = snapshot.screen == "in_run" and snapshot.player is not None
        if self.run_dir is None:
            if not in_run or snapshot.game_over:
                return
            self._start_run(snapshot)

        if status != self.bot_status:
            self.stats.setdefault("status_seconds", {})
            seconds = self.stats["status_seconds"]
            seconds[self.bot_status] = round(seconds.get(self.bot_status, 0) + now - self.status_since, 1)
            self._event("bot_status", status=status, previous=self.bot_status)
            self.bot_status, self.status_since = status, now

        if not in_run:
            # Back at the main menu without a game over: the player quit (the run stays saved).
            if snapshot.screen == "main_menu" and self.prev is not None:
                self._end_run("quit", "returned to the main menu", self.prev)
            return

        prev = self.prev
        if prev is not None and prev.player is not None:
            self._diff(prev, snapshot)          # first, so the killing blow is recorded too
        self._crew_changes(snapshot)
        if snapshot.game_over:
            outcome, cause = self._judge_end(snapshot)
            self._end_run(outcome, cause, snapshot)
            return

        if now - self.last_sample >= SAMPLE_SECONDS:
            self.samples.write(json.dumps(self._sample(snapshot, status), default=str) + "\n")
            self.samples.flush()
            self.last_sample = now
            self._unpowered_check(snapshot)
        if now - self.last_summary >= SUMMARY_SECONDS:
            self._write_summary(final=False, snapshot=snapshot)
            self._save_current(snapshot)
            self.last_summary = now
        self.prev = snapshot

    # --- what changed -------------------------------------------------------------

    def _diff(self, prev, snapshot):
        a, b = prev.player, snapshot.player
        combat = in_combat(snapshot)
        was_combat = in_combat(prev)
        enemy = snapshot.enemy

        if (snapshot.sector, snapshot.location_event) != (prev.sector, prev.location_event) or (
                b.jump_animating and not a.jump_animating):
            if snapshot.sector != prev.sector:
                self._event("sector", sector=snapshot.sector)
            if snapshot.location_event != prev.location_event:
                self._event("arrived", sector=snapshot.sector, event=snapshot.location_event,
                            hazards=snapshot.beacon_hazards, hull=b.hull, fuel=b.fuel, scrap=b.scrap)
                self.stats["jumps"] = self.stats.get("jumps", 0) + 1

        if combat and not was_combat:
            self.fight = {"enemy": enemy.blueprint, "t0": time.monotonic(), "hull0": b.hull,
                          "enemy_hull0": enemy.hull, "hits": 0, "hits_shields_down": 0, "aims": 0,
                          "failed_actions": 0, "idle_s": 0.0, "last_tick": time.monotonic()}
            self._event("combat_start", enemy=enemy.blueprint, enemy_hull=enemy.hull, enemy_shields=enemy.max_shield_layers,
                        enemy_weapons=[w.blueprint for w in enemy.weapons]
                        + [f"pod:{a.weapon.blueprint}" for a in enemy.artillery if a.weapon is not None],
                        enemy_drones=[d.blueprint for d in enemy.drones],
                        boss_stage=enemy.boss_stage or None,
                        enemy_systems={n: s.level for n, s in enemy.systems.items()}, our_hull=b.hull,
                        our_shields=b.max_shield_layers, sector=snapshot.sector)
        if self.fight is not None:
            tick = time.monotonic()
            if self.bot_status == "waiting":
                self.fight["idle_s"] += tick - self.fight["last_tick"]
            self.fight["last_tick"] = tick
            self.fight["enemy_hull_now"] = enemy.hull if enemy else 0
        if was_combat and not combat and self.fight is not None:
            last_enemy = prev.enemy
            result = ("destroyed" if (enemy is None or enemy.destroyed or (last_enemy and last_enemy.hull <= 0))
                      else "surrendered" if (enemy and enemy.surrendered)
                      else "enemy_escaped" if (last_enemy and last_enemy.escaping) else "ended")
            self._close_fight(result, b.hull)

        if b.hull < a.hull:
            shields_down = a.shield_layers == 0
            hazards = list(snapshot.beacon_hazards)
            self._event("hull_damage", amount=a.hull - b.hull, hull=b.hull, max_hull=b.max_hull,
                        shields_up=a.shield_layers, in_combat=combat, enemy=enemy.blueprint if enemy and combat else None,
                        hazards=hazards, shield_power=b.systems["shields"].power if "shields" in b.systems else None)
            if self.fight is not None:
                self.fight["hits"] += 1
                self.fight["hits_shields_down"] += shields_down
            self.stats["hull_lost"] = self.stats.get("hull_lost", 0) + a.hull - b.hull
        elif b.hull > a.hull:
            self._event("hull_repaired", amount=b.hull - a.hull, hull=b.hull)

        if b.scrap > a.scrap:
            self.stats["scrap_gained"] = self.stats.get("scrap_gained", 0) + b.scrap - a.scrap
        for name, system in b.systems.items():
            before = a.systems.get(name)
            if before is None:
                self._event("system_added", system=name, level=system.level)
            elif system.health < before.health:
                self._event("system_damage", system=name, health=system.health, level=system.level, in_combat=combat)
            elif system.health > before.health and system.health == system.level:
                self._event("system_repaired", system=name)
            elif before is not None and system.level > before.level:
                self._event("system_upgraded", system=name, level=system.level)
        old_fire = {r.room_id: r.fires for r in a.rooms}
        old_breach = {r.room_id: r.breaches for r in a.rooms}
        for room in b.rooms:
            if room.fires > old_fire.get(room.room_id, 0):
                self._event("fire", room=room.room_id, tiles=room.fires)
            if room.breaches > old_breach.get(room.room_id, 0):
                self._event("breach", room=room.room_id, count=room.breaches)
        low, was_low = (b.oxygen_total or 1) < 0.5, (a.oxygen_total or 1) < 0.5
        if low != was_low:
            self._event("oxygen_low" if low else "oxygen_ok", total=b.oxygen_total,
                        oxygen_system=(b.systems["oxygen"].power, b.systems["oxygen"].health) if "oxygen" in b.systems else None)
        boarders = self._intruders(snapshot)
        old_boarders = self._intruders(prev)
        if bool(boarders) != bool(old_boarders):
            if boarders:
                self._event("boarded", count=len(boarders), rooms=sorted({c.room_id for c in boarders}),
                            species=[c.species for c in boarders])
                self.stats["boardings"] = self.stats.get("boardings", 0) + 1
            else:
                self._event("boarders_gone")
        if snapshot.paused != prev.paused:
            self._event("paused" if snapshot.paused else "unpaused", focus_pause=snapshot.focus_paused)
        if b.weapons != a.weapons and [w.blueprint for w in b.weapons] != [w.blueprint for w in a.weapons]:
            self._event("weapons_changed", weapons=[w.blueprint for w in b.weapons])
        if [d.blueprint for d in b.drones] != [d.blueprint for d in a.drones]:
            self._event("drones_changed", drones=[d.blueprint for d in b.drones])

    def _close_fight(self, result: str, hull_now: int):
        fight = self.fight
        record = {"enemy": fight["enemy"], "result": result, "seconds": round(time.monotonic() - fight["t0"], 1),
                  "hull_lost": fight["hull0"] - hull_now, "hits_taken": fight["hits"],
                  "hits_with_shields_down": fight["hits_shields_down"], "aims": fight["aims"],
                  "failed_actions": fight["failed_actions"], "bot_idle_s": round(fight["idle_s"], 1),
                  "enemy_hull": [fight.get("enemy_hull_now"), fight["enemy_hull0"]]}
        self._event("combat_end", **record)
        self.stats.setdefault("fights", []).append(record)
        self.fight = None

    def _intruders(self, snapshot) -> list:
        player = snapshot.player
        crew = list(player.crew) + (list(snapshot.enemy.crew) if snapshot.enemy else [])
        return [c for c in crew if c.ship_id == player.ship_id and not c.dead and c.intruder]

    def _crew_changes(self, snapshot):
        player = snapshot.player
        own = {c.address: c for c in _crew_list(player) if c.ship_id == player.ship_id and not c.intruder}
        alive = {a: c for a, c in own.items() if not c.dead}
        rooms = {r.room_id: r for r in player.rooms}
        for address, before in self.prev_crew.items():
            if address in alive:
                continue
            room = rooms.get(before.room_id)
            boarders_there = [c for c in self._intruders(snapshot) if c.room_id == before.room_id]
            if room is not None and room.oxygen is not None and room.oxygen < 10:
                cause = f"suffocation (room {before.room_id} at {room.oxygen:.0f}% air)"
            elif room is not None and room.fires:
                cause = f"fire (room {before.room_id}, {room.fires} burning tiles)"
            elif boarders_there or before.fighting:
                cause = f"killed fighting boarders in room {before.room_id}"
            else:
                cause = f"unknown (last seen in room {before.room_id} at {before.health:.0f} hp)"
            self._event("crew_died", name=before.name, species=before.species, room=before.room_id,
                        last_hp=round(before.health), cause=cause,
                        room_oxygen=room.oxygen if room else None, room_fires=room.fires if room else None)
            self.stats.setdefault("crew_deaths", []).append({"name": before.name, "cause": cause})
        for address, crew in alive.items():
            if self.prev_crew and address not in self.prev_crew:
                self._event("crew_joined", name=crew.name, species=crew.species)
        self.prev_crew = alive

    def _unpowered_check(self, snapshot):
        """Count samples where an important system sat unpowered while the reactor had spare bars."""
        player = snapshot.player
        if player.reactor_total is None or player.reactor_used is None:
            return
        spare = player.reactor_total - player.reactor_used
        for name in ("shields", "oxygen", "engines"):
            system = player.systems.get(name)
            if system is None:
                continue
            usable = min(system.level, system.health)
            if name == "shields":
                usable -= usable % 2          # a shield layer takes two bars: an odd bar is no loss
            need = 2 if name == "shields" else 1
            if system is not None and spare >= need and system.power < usable and not system.ion_lock:
                key = f"{name}_underpowered_samples"
                self.stats[key] = self.stats.get(key, 0) + 1

    def _sample(self, snapshot, status: str) -> dict:
        player, enemy = snapshot.player, snapshot.enemy
        return {
            "t": _now_iso(), "status": status, "sector": snapshot.sector, "event": snapshot.location_event,
            "paused": snapshot.paused, "combat": in_combat(snapshot),
            "hull": player.hull, "shields": [player.shield_layers, player.max_shield_layers],
            "scrap": player.scrap, "fuel": player.fuel, "missiles": player.missiles, "drone_parts": player.drone_parts,
            "reactor": [player.reactor_used, player.reactor_total], "oxygen": player.oxygen_total,
            "systems": {n: [s.power, s.level, s.health] for n, s in player.systems.items()},
            "crew": [[c.name, round(c.health), c.room_id, c.manning] for c in _crew_list(player)
                     if c.ship_id == player.ship_id and not c.dead],
            "weapons": [[w.blueprint, w.powered, round(w.charge, 1)] for w in player.weapons],
            "drones": [[d.blueprint, d.powered, d.deployed] for d in player.drones],
            "fires": sum(r.fires for r in player.rooms), "breaches": sum(r.breaches for r in player.rooms),
            "boarders": len(self._intruders(snapshot)),
            "enemy": None if enemy is None else {
                "ship": enemy.blueprint, "hull": enemy.hull, "shields": [enemy.shield_layers, enemy.max_shield_layers],
                "systems": {n: [s.power, s.level, s.health] for n, s in enemy.systems.items()},
                "weapons": [w.blueprint for w in enemy.weapons],
                "pods": [[a.weapon.blueprint if a.weapon else "?", a.health, a.level] for a in enemy.artillery],
                "boss_stage": enemy.boss_stage or None, "surge_in": enemy.surge_in},
        }

    # --- the verdict ------------------------------------------------------------------

    def _judge_end(self, snapshot) -> tuple[str, str]:
        player = snapshot.player
        reason, self.ftl_reason = self.ftl_reason, ""
        # GameOver.gameoverText holds the sentence shown, not its id: map it back.
        lowered = reason.lower()
        if "crewmembers have died" in lowered or "crew" in lowered and "died" in lowered:
            reason = "gameover_crew"
        elif "federation base" in lowered or "base" in lowered and "destroyed" in lowered:
            reason = "gameover_fedbase"
        elif "victory" in lowered or "flagship" in lowered and "destroyed" in lowered:
            reason = "gameover_win"
        named = {"gameover_win": ("victory", "the Rebel flagship was destroyed"),
                 "gameover_fedbase": ("base_destroyed", "the flagship destroyed the Federation base")}
        if reason in named:
            return named[reason]
        if reason == "gameover_crew" and player.hull > 0:
            deaths = self.stats.get("crew_deaths", [])[-4:]
            return "crew_lost", "all crew dead: " + "; ".join(f"{d['name']}: {d['cause']}" for d in deaths)
        alive = [c for c in _crew_list(player) if c.ship_id == player.ship_id and not c.dead and not c.intruder]
        if player.hull <= 0:
            enemy = snapshot.enemy.blueprint if snapshot.enemy else None
            hazards = ", ".join(snapshot.beacon_hazards)
            return "destroyed", f"hull reached 0" + (f" fighting {enemy}" if enemy else "") + (f" ({hazards})" if hazards else "")
        if not alive:
            deaths = self.stats.get("crew_deaths", [])[-4:]
            return "crew_lost", "all crew dead: " + "; ".join(f"{d['name']}: {d['cause']}" for d in deaths)
        if (snapshot.sector or 0) >= 8:
            return "victory_or_base_lost", "game over in the last sector"
        return "game_over", "game over (reason not identified)"

    def _write_summary(self, final: bool, snapshot=None) -> dict:
        stats = self.stats
        fights = stats.get("fights", [])
        good, bad = [], []
        wins = [f for f in fights if f["result"] in ("destroyed", "surrendered")]
        if wins:
            good.append(f"won {len(wins)} of {len(fights)} fights")
        if stats.get("scrap_gained"):
            good.append(f"collected {stats['scrap_gained']} scrap")
        if stats.get("purchases"):
            good.append(f"made {stats['purchases']} store purchases")
        costly = sorted(fights, key=lambda f: f["hull_lost"], reverse=True)[:3]
        for fight in costly:
            if fight["hull_lost"] >= 8 or fight["result"] == "we_lost":
                bad.append(f"lost {fight['hull_lost']} hull fighting {fight['enemy']} "
                           f"({fight['hits_with_shields_down']} of {fight['hits_taken']} hits with shields down, "
                           f"{fight['seconds']}s, {fight.get('aims', '?')} aims, bot idle {fight.get('bot_idle_s', 0)}s, "
                           f"enemy hull {fight.get('enemy_hull')}, result {fight['result']})")
        escaped = [f for f in fights if f["result"] == "enemy_escaped"]
        if escaped:
            bad.append(f"{len(escaped)} enemies escaped")
        for name in ("shields", "oxygen", "engines"):
            samples = stats.get(f"{name}_underpowered_samples", 0)
            if samples >= 5:
                bad.append(f"{name} underpowered with spare reactor for ~{samples * SAMPLE_SECONDS:.0f}s")
        seconds = dict(stats.get("status_seconds", {}))
        seconds[self.bot_status] = seconds.get(self.bot_status, 0) + time.monotonic() - self.status_since
        idle = seconds.get("waiting", 0)
        if idle > 60:
            bad.append(f"bot could not play (FTL not focused) for {idle:.0f}s")
        for death in stats.get("crew_deaths", []):
            bad.append(f"crew {death['name']} died: {death['cause']}")
        if stats.get("failed_actions", 0) >= 5:
            bad.append(f"{stats['failed_actions']} bot actions failed (see failure_examples)")
        summary = {
            "run": {k: v for k, v in self.run.items() if not k.startswith("_")},
            "final": final,
            "sector_reached": (snapshot.sector if snapshot else None) or self.run.get("start_sector"),
            "good": good, "bad": bad, "stats": stats,
            "last_events": [e for e in list(self.tail)[-400:]
                            if e["kind"] != "bot" or any(w in e.get("text", "") for w in
                                                         ("COMBAT", "target", "door", "crew_to_room", "EVENT", "power", "drone",
                                                          "failed", "did not", "escape", "ESCAPE", "waiting"))][-150:],
        }
        if self.run_dir is not None:
            (self.run_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
        return summary
