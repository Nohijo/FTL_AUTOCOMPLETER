# Run logs (for Claude)

The bot (`bot.py --live`) records every run here through `observer/run_log.py`.
Start with `POSTMORTEMS.md` (regenerate with `python3 postmortem.py`), then open a
run's `summary.json`, then its `events.jsonl` around the times that matter.

## Files

- `index.jsonl`: one line per finished run: `id, ended, blueprint, outcome, cause, sector, dir`.
- `current.json`: the run in progress (used to resume the same run when the bot restarts).
- `<YYYYmmdd-HHMMSS>_<SHIP_BLUEPRINT>/`
  - `run.json`: metadata: start time, ship, start sector, `attached_mid_run`, start crew,
    weapons, drones, system levels, running `stats`.
  - `events.jsonl`: one JSON object per line in time order. Every record has
    `t` (local ISO time), `run_s` (seconds since this bot session attached) and `kind`.
  - `samples.jsonl`: full ship state every 3 s (see below).
  - `summary.json`: `run`, `final` (true once the run ended), `sector_reached`,
    `good`/`bad` (plain-language findings), `stats`, `last_events` (the tail).

## Event kinds (`events.jsonl`)

| kind | fields |
|---|---|
| run_start / run_resumed | the run.json metadata |
| arrived | sector, event (location event name), hazards, hull, fuel, scrap |
| sector | sector |
| combat_start | enemy, enemy_hull, enemy_shields, enemy_weapons, enemy_drones, enemy_systems, our_hull, our_shields, sector |
| combat_end | enemy, result (destroyed / surrendered / enemy_escaped / ended / we_lost / unfinished), seconds, hull_lost, hits_taken, hits_with_shields_down, aims (successful weapon aims), failed_actions, bot_idle_s, enemy_hull [now, start] |
| hull_damage | amount, hull, max_hull, shields_up (layers up just before the hit), in_combat, enemy, hazards, shield_power |
| hull_repaired | amount, hull |
| system_damage / system_repaired / system_upgraded / system_added | system, health, level |
| fire / breach | room, tiles / count |
| oxygen_low / oxygen_ok | total (0-1), oxygen_system [power, health] |
| boarded / boarders_gone | count, rooms, species |
| crew_died | name, species, room, last_hp, cause (suffocation / fire / killed fighting boarders / unknown), room_oxygen (%), room_fires |
| crew_joined | name, species |
| paused / unpaused | focus_pause (true = FTL paused itself on losing focus) |
| bot_status | status: active (FTL focused, bot playing), background (playing through direct input), waiting (bot not playing) |
| weapons_changed / drones_changed | the new lists |
| bot | text: every line the bot printed (decisions, results, failures) |
| bot_stopped | reason (the run may continue in a later session) |
| run_end | outcome (destroyed / crew_lost / quit / victory_or_base_lost / game_over), cause |
| recorder_error | error, where (a bug in the recorder; the bot kept running) |

## Samples (`samples.jsonl`)

`t, status, sector, event, paused, combat, hull, shields [up, max], scrap, fuel, missiles,
drone_parts, reactor [used, total], oxygen (0-1), systems {name: [power, level, health]},
crew [[name, hp, room, manning system id]], weapons [[blueprint, powered, charge s]],
drones [[blueprint, powered, deployed]], fires, breaches, boarders,
enemy {ship, hull, shields, systems, weapons} or null`.

Room oxygen is in percent (0-100); the ship total `oxygen` is a fraction (0-1).

## Reading a failure

1. `summary.json` -> `run.cause`, `bad`, the last `combat_end`.
2. Check `bot_status` events: losses while the bot was `waiting` are not strategy failures.
3. Around the fatal moment in `events.jsonl`: which systems were damaged first, whether
   shields were up for the hits, what the bot tried (`bot` lines) and which actions failed.
4. `samples.jsonl` to see power allocation, crew positions and the enemy's state over time.
