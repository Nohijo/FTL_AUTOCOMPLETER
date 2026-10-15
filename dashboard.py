"""Live dashboard for the FTL bot, in a browser tab: http://127.0.0.1:8765

    ftl-dash                  start it in the background (returns at once)
    ftl-dash --foreground     same, but stay attached to this terminal
    ftl-dash --stop           stop the background dashboard

You rarely need to call it yourself: ftl-bot starts it. It shows what the bot
prints (data/logs/bot.log, written by bot.py) next to the ship's live status
read from FTL through ftl-host. It only reads; it never sends anything to the
game. Add panels here as the bot grows.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import struct
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import bot_settings
from config import LOG_DIR, PROJECT_DIR
from ftl_host import SOCKET_PATH, host_is_up
from observer.memory import layout as L
from observer.memory.adapter import in_combat
from observer.memory.process import AttachError, HostClient, MemoryReadError
from observer.memory.reader import read_snapshot

PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
LOG_FILE = LOG_DIR / "bot.log"
DASH_LOG = LOG_DIR / "dashboard.log"
PID_FILE = PROJECT_DIR / "data" / "dashboard.pid"
INITIAL_LINES = 400


class GameLink:
    """One shared connection to ftl-host, re-opened when FTL restarts."""

    def __init__(self):
        self.lock = threading.Lock()
        self.client = None
        self.clock = None                  # (real time, FTL's game clock) at the last speed sample
        self.speed_real = None

    def _measure_speed(self) -> float | None:
        """The speed the game really runs at: game seconds per real second, from FTL's own
        game clock (CFPS: +0x0 grows by speedFactor/16 every unpaused frame). The speed
        setting alone overstates it: FTL caps the game time of one frame, so past a point
        only a higher frame rate makes the game faster. 0 while paused."""
        now = time.monotonic()
        clock = struct.unpack("<f", self.client.read(L.CFPS, 4))[0]
        if self.clock is not None and now - self.clock[0] >= 0.5:
            rate = max(0.0, (clock - self.clock[1]) / (now - self.clock[0]))
            self.speed_real = rate if self.speed_real is None else 0.5 * self.speed_real + 0.5 * rate
            self.clock = (now, clock)
        elif self.clock is None or clock < self.clock[1]:
            self.clock = (now, clock)
        return self.speed_real

    def state(self) -> dict:
        with self.lock:
            for _ in range(2):
                try:
                    if self.client is None:
                        if not host_is_up():
                            return {"connected": False, "reason": "FTL is not running through ftl-host"}
                        self.client = HostClient(SOCKET_PATH)
                    state = summarize(read_snapshot(self.client))
                    state["speed_level"] = self.client.i32(L.CFPS_SPEED_LEVEL)
                    state["speed_real"] = self._measure_speed()
                    state["fps"] = self.client.i32(L.CFPS_FPS)
                    return state
                except (AttachError, MemoryReadError, OSError) as error:
                    self.client = None
                    reason = str(error)
            return {"connected": False, "reason": reason}

    def apply_speed(self, level: int) -> str | None:
        with self.lock:
            if self.client is None:
                return None          # the bot applies it once FTL is running
            return bot_settings.apply_speed(self.client, level)


def summarize(s) -> dict:
    """The parts of a snapshot the page shows."""
    def ship(info):
        if info is None:
            return None
        return {
            "name": info.name or info.blueprint, "hull": info.hull, "max_hull": info.max_hull,
            "shields": info.shield_layers, "max_shields": info.max_shield_layers,
            "scrap": info.scrap, "fuel": info.fuel, "missiles": info.missiles, "drones": info.drone_parts,
            "reactor": [info.reactor_used, info.reactor_total], "oxygen": info.oxygen_total,
            "ftl": [round(info.jump_charge, 1), round(info.jump_goal, 1)],
            "destroyed": info.destroyed, "surrendered": info.surrendered, "escaping": info.escaping,
            "systems": [{"name": n, "power": v.power, "level": v.level, "health": v.health,
                         "fire": v.on_fire, "breach": v.breached, "ion": v.ion_lock}
                        for n, v in sorted(info.systems.items(), key=lambda kv: kv[1].type_id)],
            "weapons": [{"name": w.blueprint, "powered": w.powered, "charge": round(w.charge, 1),
                         "goal": round(w.charge_goal, 1), "ready": w.ready} for w in info.weapons],
            "crew": [{"name": c.name, "species": c.species, "hp": round(c.health), "max_hp": round(c.max_health),
                      "room": c.room_id, "manning": c.manning, "intruder": c.intruder,
                      "fighting": c.fighting, "repairing": c.repairing, "extinguishing": c.extinguishing}
                     for c in info.crew if c.room_id >= 0 and not c.dead],
            "fires": sum(r.fires for r in info.rooms), "breaches": sum(r.breaches for r in info.rooms),
        }
    return {
        "connected": True, "screen": s.screen, "sector": s.sector, "paused": s.paused,
        "combat": in_combat(s) if s.player else False, "event_open": s.event_open, "event_text": s.event_text,
        "store_open": s.store_open, "map_open": s.map_open, "hazards": s.beacon_hazards,
        "location": s.location_event, "player": ship(s.player), "enemy": ship(s.enemy),
        "bar": list(s.mapping.bar),
    }


def read_log(offset: int | None) -> dict:
    if not LOG_FILE.exists():
        return {"offset": 0, "lines": []}
    size = LOG_FILE.stat().st_size
    with open(LOG_FILE, "rb") as handle:
        if offset is None or offset > size:
            handle.seek(max(0, size - 200_000))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()[-INITIAL_LINES:]
        else:
            handle.seek(offset)
            data = handle.read(size - offset)
            size = offset + data.rfind(b"\n") + 1  # leave a half-written last line for the next poll
            lines = data[:size - offset].decode("utf-8", errors="replace").splitlines()
    return {"offset": size, "lines": lines}


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FTL Bot</title>
<style>
:root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --text:#e6edf3; --dim:#8b949e;
        --green:#3fb950; --red:#f85149; --yellow:#d29922; --blue:#58a6ff; --purple:#bc8cff; --cyan:#39c5cf; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.4 system-ui, sans-serif; }
header { display:flex; gap:16px; align-items:center; padding:10px 16px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
header h1 { font-size:16px; margin:0; }
.pill { padding:2px 10px; border-radius:999px; background:var(--panel); border:1px solid var(--line); color:var(--dim); }
.pill.on { color:var(--text); border-color:var(--blue); }
.pill.bad { color:var(--red); border-color:var(--red); }
.pill:empty { display:none; }
#controls { display:flex; gap:8px; align-items:center; padding:8px 16px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
button { background:var(--panel); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:4px 10px; font:inherit; cursor:pointer; }
button.on { border-color:var(--green); color:var(--green); }
button:hover { border-color:var(--blue); }
main { display:grid; grid-template-columns:minmax(300px, 420px) 1fr; gap:12px; padding:12px; height:calc(100vh - 100px); }
@media (max-width: 800px) { main { grid-template-columns:1fr; height:auto; } #log { height:60vh; } }
.side { overflow:auto; display:flex; flex-direction:column; gap:12px; }
section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
section h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--dim); margin:0 0 8px; }
.bar { height:10px; background:#21262d; border-radius:5px; overflow:hidden; margin:4px 0 8px; }
.bar > div { height:100%; background:var(--green); }
.grid { display:grid; grid-template-columns:repeat(4, 1fr); gap:6px; }
.stat { background:#0d1117; border-radius:6px; padding:6px; text-align:center; }
.stat b { display:block; font-size:16px; }
.stat span { color:var(--dim); font-size:11px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
td { padding:3px 4px; border-top:1px solid var(--line); }
.dim { color:var(--dim); }
#log { background:#010409; border:1px solid var(--line); border-radius:8px; padding:8px 10px; overflow:auto;
       font:12.5px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; white-space:pre-wrap; word-break:break-word; }
.l-combat { color:var(--red); } .l-event { color:var(--blue); } .l-jump { color:var(--green); }
.l-store { color:var(--yellow); } .l-upgrade { color:var(--purple); } .l-crew { color:var(--cyan); }
.l-door { color:#ff9e64; } .l-bad { color:var(--red); font-weight:600; } .l-advice { color:var(--dim); }
</style></head><body>
<header><h1>FTL Bot</h1><span id="conn" class="pill">connecting…</span><span id="where" class="pill"></span>
<span id="flags"></span><label class="dim"><input type="checkbox" id="follow" checked> follow log</label></header>
<nav id="controls">
  <button id="autoplay" title="On: the bot plays, also while FTL is behind other windows. Off: manual control (F9 in the game)">Auto-play: …</button>
  <button id="overlay" title="Show the bot's status in the black bars above and below the game">Overlay on game: …</button>
  <span class="dim">Game speed setting</span><span id="speeds"></span>
  <span id="real-speed" class="pill" title="Measured from FTL's game clock. FTL caps the game time of one frame, so past a point a higher setting adds nothing: only more frames per second do."></span>
  <span id="settings-note" class="dim"></span>
</nav>
<main>
  <div class="side">
    <section><h2>Ship</h2><div id="ship" class="dim">—</div></section>
    <section><h2>Crew</h2><div id="crew" class="dim">—</div></section>
    <section><h2>Systems &amp; weapons</h2><div id="systems" class="dim">—</div></section>
    <section><h2>Enemy</h2><div id="enemy" class="dim">none</div></section>
  </div>
  <div id="log"></div>
</main>
<script>
const SYS = {0:"shields",1:"engines",2:"oxygen",3:"weapons",4:"drones",5:"medbay",6:"helm",7:"sensors",8:"doors"};
const esc = s => String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
function bar(v, max, color) { const p = max ? Math.max(0, Math.min(100, 100*v/max)) : 0;
  return `<div class="bar"><div style="width:${p}%;background:${color || (p < 35 ? "var(--red)" : p < 65 ? "var(--yellow)" : "var(--green)")}"></div></div>`; }
function ship(s) {
  return `<div>${esc(s.name)} — hull <b>${s.hull}/${s.max_hull}</b></div>${bar(s.hull, s.max_hull)}
  <div class="grid">
    <div class="stat"><b>${s.shields}/${s.max_shields}</b><span>shields</span></div>
    <div class="stat"><b>${s.scrap}</b><span>scrap</span></div>
    <div class="stat"><b>${s.fuel}</b><span>fuel</span></div>
    <div class="stat"><b>${s.missiles}</b><span>missiles</span></div>
    <div class="stat"><b>${s.drones}</b><span>drone parts</span></div>
    <div class="stat"><b>${s.reactor[0]}/${s.reactor[1]}</b><span>reactor</span></div>
    <div class="stat"><b>${s.oxygen == null ? "?" : Math.round(s.oxygen * 100) + "%"}</b><span>oxygen</span></div>
    <div class="stat"><b>${s.ftl[1] && s.ftl[0] >= s.ftl[1] ? "READY" : Math.round(100 * s.ftl[0] / (s.ftl[1] || 1)) + "%"}</b><span>FTL</span></div>
  </div>${s.fires || s.breaches ? `<div style="margin-top:6px;color:var(--red)">🔥 ${s.fires} fire tiles · ${s.breaches} breach${s.breaches === 1 ? "" : "es"}</div>` : ""}`; }
function crew(list) {
  if (!list.length) return "—";
  return "<table>" + list.map(c => {
    const what = c.intruder ? "<b style='color:var(--red)'>INTRUDER</b>" : c.fighting ? "fighting" : c.extinguishing ? "fire" :
      c.repairing ? "repairing" : c.manning >= 0 ? "at " + (SYS[c.manning] || c.manning) : "";
    return `<tr><td>${esc(c.name)}</td><td class="dim">${esc(c.species)}</td><td style="width:90px">${bar(c.hp, c.max_hp)}</td>
            <td class="dim">room ${c.room}</td><td>${what}</td></tr>`; }).join("") + "</table>"; }
function systems(s) {
  const rows = s.systems.map(x => `<tr><td>${esc(x.name)}</td><td>${x.power}/${x.level}</td>
     <td style="color:${x.health < x.level ? "var(--red)" : "var(--dim)"}">${x.health < x.level ? "damaged " + x.health + "/" + x.level : "ok"}</td>
     <td>${x.fire ? "🔥" : ""}${x.breach ? "💨" : ""}${x.ion ? "⚡" : ""}</td></tr>`).join("");
  const weps = s.weapons.map(w => `<tr><td>${esc(w.name)}</td><td colspan="3">${w.powered ? (w.ready ? "<b style='color:var(--green)'>READY</b>" : w.charge + "/" + w.goal + "s") : "<span class='dim'>off</span>"}</td></tr>`).join("");
  return `<table>${rows}${weps}</table>`; }
function kind(line) {
  if (/failed|not found|did not|not executed|lost focus|could not|stuck/i.test(line)) return "l-bad";
  if (/COMBAT|target:|ESCAPE|power_weapon/.test(line)) return "l-combat";
  if (/EVENT|event:/.test(line)) return "l-event";
  if (/JUMP|jump:|next sector/.test(line)) return "l-jump";
  if (/STORE|store:/.test(line)) return "l-store";
  if (/UPGRADE|upgrade:/.test(line)) return "l-upgrade";
  if (/door:/.test(line)) return "l-door";
  if (/crew_to_room|medbay|station/.test(line)) return "l-crew";
  if (/advice:/.test(line)) return "l-advice";
  return ""; }
let offset = null;
async function pollLog() {
  try {
    const r = await (await fetch("/api/log" + (offset == null ? "" : "?from=" + offset))).json();
    offset = r.offset;
    if (r.lines.length) {
      const log = document.getElementById("log");
      log.insertAdjacentHTML("beforeend", r.lines.map(l => `<div class="${kind(l)}">${esc(l)}</div>`).join(""));
      while (log.childElementCount > 3000) log.firstChild.remove();
      if (document.getElementById("follow").checked) log.scrollTop = log.scrollHeight;
    }
  } catch (e) {}
}
async function pollState() {
  let s;
  try { s = await (await fetch("/api/state")).json(); } catch (e) { s = {connected:false, reason:"dashboard server not reachable"}; }
  const conn = document.getElementById("conn");
  conn.textContent = s.connected ? "connected" : "not connected: " + (s.reason || "");
  conn.className = "pill " + (s.connected ? "on" : "bad");
  if (!s.connected) return;
  if (s.speed_real != null)
    document.getElementById("real-speed").textContent = s.speed_real < 0.05 ? "real speed: paused"
      : `real speed: ${s.speed_real.toFixed(1)}× (${s.fps} fps)`;
  document.getElementById("where").textContent = s.screen === "in_run" ? `sector ${s.sector} · ${s.location || "beacon"}` : s.screen;
  const flags = [["COMBAT", s.combat, true], ["paused", s.paused], ["event", s.event_open], ["store", s.store_open], ["map", s.map_open]]
    .filter(f => f[1]).map(f => `<span class="pill ${f[2] ? "bad" : "on"}">${f[0]}</span>`);
  (s.hazards || []).forEach(h => flags.push(`<span class="pill bad">${esc(h)}</span>`));
  document.getElementById("flags").innerHTML = flags.join(" ");
  if (s.player) {
    document.getElementById("ship").innerHTML = ship(s.player);
    document.getElementById("crew").innerHTML = crew(s.player.crew);
    document.getElementById("systems").innerHTML = systems(s.player);
  }
  document.getElementById("enemy").innerHTML = s.enemy && !s.enemy.destroyed
    ? ship(s.enemy) + (s.enemy.surrendered ? "<div>surrendered</div>" : "") + (s.enemy.escaping ? "<div style='color:var(--red)'>escaping!</div>" : "")
      + crew(s.enemy.crew)
    : "none";
}
const SPEEDS = [[0, "1×"], [1, "1.4×"], [2, "1.9×"], [3, "2.3×"], [5, "3.2×"], [7, "4.1×"],
                [10, "5.4×"], [14, "7.1×"], [18, "8.9×"], [21, "10.2×"],
                [30, "14.1×"], [42, "19.4×"], [58, "26.4×"], [78, "35.1×"], [112, "50×"]];
let settings = {};
function showSettings(s, note) {
  settings = s;
  const ap = document.getElementById("autoplay"), ov = document.getElementById("overlay");
  ap.textContent = "Auto-play: " + (s.autoplay ? "ON" : "OFF (manual control)"); ap.className = s.autoplay ? "on" : "";
  ov.textContent = "Overlay on game: " + (s.overlay ? "ON" : "OFF"); ov.className = s.overlay ? "on" : "";
  document.getElementById("speeds").innerHTML = SPEEDS.map(([level, label]) =>
    `<button data-level="${level}" class="${s.speed === level ? "on" : ""}">${label}</button>`).join(" ");
  document.getElementById("settings-note").textContent = note ||
    (s.speed > 2 ? "above 1.9× FTL needs VSync and Frame Limit off (FTL options) to reach full speed" : "");
}
async function change(update) {
  try {
    const r = await (await fetch("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"},
                                                   body: JSON.stringify(update)})).json();
    showSettings(r.settings, r.warning);
  } catch (e) { document.getElementById("settings-note").textContent = "could not save: " + e; }
}
document.getElementById("autoplay").onclick = () => change({autoplay: !settings.autoplay});
document.getElementById("overlay").onclick = () => change({overlay: !settings.overlay});
document.getElementById("speeds").onclick = e => { const b = e.target.closest("button"); if (b) change({speed: +b.dataset.level}); };
fetch("/api/settings").then(r => r.json()).then(r => showSettings(r.settings)).catch(() => {});
pollLog(); pollState();
setInterval(pollLog, 1000); setInterval(pollState, 1000);
</script></body></html>
"""


def make_handler(link: GameLink):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                self._send(PAGE.encode(), "text/html; charset=utf-8")
            elif url.path == "/api/ping":
                self._send(b"ftl-dash", "text/plain")
            elif url.path == "/api/settings":
                self._send(json.dumps({"settings": bot_settings.load()}).encode(), "application/json")
            elif url.path == "/api/state":
                self._send(json.dumps(link.state()).encode(), "application/json")
            elif url.path == "/api/log":
                offset = parse_qs(url.query).get("from", [None])[0]
                self._send(json.dumps(read_log(int(offset) if offset else None)).encode(), "application/json")
            else:
                self.send_error(404)

        def do_POST(self):
            if urlparse(self.path).path != "/api/settings":
                self.send_error(404)
                return
            # Only this page may change settings: other sites cannot send a JSON POST
            # here without a CORS preflight (never answered), and the Origin must match.
            origin = self.headers.get("Origin")
            if self.headers.get("Content-Type") != "application/json" or (origin and origin not in (URL, f"http://localhost:{PORT}")):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                update = json.loads(self.rfile.read(min(length, 4096)) or b"{}")
                if not isinstance(update, dict):
                    raise ValueError("expected an object")
            except ValueError as error:
                self.send_error(400, str(error))
                return
            settings = bot_settings.save(update)
            warning = link.apply_speed(settings["speed"]) if "speed" in update else None
            self._send(json.dumps({"settings": settings, "warning": warning}).encode(), "application/json")

        def log_message(self, *args):
            pass  # keep the terminal quiet

    return Handler


def dashboard_is_up() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/ping", timeout=1) as reply:
            return reply.read() == b"ftl-dash"
    except OSError:
        return False


def ensure_dashboard(timeout: float = 5.0) -> bool:
    """Start the dashboard in the background unless it is already serving."""
    if dashboard_is_up():
        return True
    DASH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DASH_LOG, "ab") as log:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--foreground"],
            cwd=PROJECT_DIR, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if dashboard_is_up():
            return True
        time.sleep(0.2)
    return False


def stop_dashboard() -> bool:
    try:
        pid = int(PID_FILE.read_text())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        return False
    PID_FILE.unlink(missing_ok=True)
    return True


def serve():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(GameLink()))
    except OSError as error:
        print(f"[dash] Cannot listen on {URL}: {error}", flush=True)
        sys.exit(1)
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"[dash] FTL bot dashboard on {URL}", flush=True)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        print("[dash] Stopped.", flush=True)
    finally:
        server.server_close()
        if PID_FILE.exists() and PID_FILE.read_text() == str(os.getpid()):
            PID_FILE.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--foreground", action="store_true", help="stay attached to this terminal")
    parser.add_argument("--stop", action="store_true", help="stop the background dashboard")
    args = parser.parse_args()

    if args.stop:
        print("[dash] Stopped." if stop_dashboard() else "[dash] Not running.")
    elif args.foreground:
        serve()
    elif ensure_dashboard():
        print(f"[dash] Running in the background: open {URL}  (stop it with: ftl-dash --stop)")
    else:
        print(f"[dash] The dashboard did not come up; see {DASH_LOG}")
        sys.exit(1)


if __name__ == "__main__":
    main()
