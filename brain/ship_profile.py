"""Adapt the plan to the ship being flown, and to what killed this ship before.

Two sources:
  - the ship's loadout: a drone ship (Engi) needs its drone system powered early, a ship
    with cloaking or hacking should grow those systems, a beam ship needs weapon power
  - its recorded runs (data/runs/*/summary.json, see observer/run_log.py): recurring causes
    of failure move the matching upgrades earlier

profile_for(blueprint) reads the history once per run; adapt_upgrade_steps() rewrites the
upgrade order of brain/upgrader.py with it. The changes are listed in `notes`, which the
bot prints when a run starts (and the run log records).
"""

import json
from dataclasses import dataclass, field

from observer.run_log import RUNS_DIR


@dataclass
class ShipProfile:
    blueprint: str
    runs: int = 0
    wins: int = 0
    sector_avg: float = 0.0
    shield_down_hits: int = 0
    hits: int = 0
    escapes: int = 0
    boarder_deaths: int = 0
    air_deaths: int = 0
    fire_deaths: int = 0
    losses_in_fights: int = 0
    notes: list[str] = field(default_factory=list)


def profile_for(blueprint: str) -> ShipProfile:
    profile = ShipProfile(blueprint)
    sectors = []
    for summary_file in sorted(RUNS_DIR.glob("*/summary.json")) if RUNS_DIR.exists() else []:
        try:
            summary = json.loads(summary_file.read_text())
        except (OSError, ValueError):
            continue
        run = summary.get("run", {})
        if run.get("blueprint") != blueprint or not summary.get("final"):
            continue
        profile.runs += 1
        profile.wins += run.get("outcome") == "victory"
        sectors.append(summary.get("sector_reached") or 0)
        stats = summary.get("stats", {})
        for fight in stats.get("fights", []):
            profile.hits += fight.get("hits_taken", 0)
            profile.shield_down_hits += fight.get("hits_with_shields_down", 0)
            profile.escapes += fight.get("result") == "enemy_escaped"
            profile.losses_in_fights += fight.get("result") == "we_lost"
        for death in stats.get("crew_deaths", []):
            cause = death.get("cause", "")
            profile.boarder_deaths += "boarders" in cause
            profile.air_deaths += "suffocation" in cause
            profile.fire_deaths += "fire" in cause
    profile.sector_avg = sum(sectors) / len(sectors) if sectors else 0.0
    return profile


def adapt_upgrade_steps(steps: list[tuple], profile: ShipProfile | None, levels: dict[str, int],
                        ship) -> list[tuple]:
    """Insert or move steps of the upgrade order. Steps are (system, target level, reason)."""
    early: list[tuple] = []
    notes: list[str] = []

    # Loadout: use what this ship is built around.
    drones = [d for d in ship.drones if not d.dead]
    if "drones" in levels and drones:
        early.append(("drones", min(8, max(levels["drones"], 2 + len(drones))), "power for this ship's drones"))
        notes.append("drone ship: drone system upgrades early")
    if any(w.weapon_type == "BEAM" for w in ship.weapons):
        notes.append("beam weapons: fired once the volley has dropped the shields")
    late: list[tuple] = []
    if "cloaking" in levels:
        late.append(("cloaking", 2, "longer cloak to dodge volleys"))
    if "hacking" in levels:
        late.append(("hacking", 2, "stronger hacking"))

    # History: fix what keeps killing this ship.
    if profile is not None and profile.runs:
        if profile.hits >= 10 and profile.shield_down_hits / max(1, profile.hits) > 0.4:
            early.append(("shields", 6, "past runs took most hits with shields down"))
            notes.append(f"{profile.shield_down_hits}/{profile.hits} past hits landed with shields down: shields first")
        if profile.escapes >= 3:
            early.append(("weapons", max(levels.get("weapons", 1), 4), "past enemies escaped: more firepower"))
            notes.append(f"{profile.escapes} enemies escaped before: weapons earlier")
        if profile.boarder_deaths >= 2:
            early.append(("doors", 3, "past crew lost to boarders"))
            notes.append(f"{profile.boarder_deaths} crew lost to boarders: doors earlier")
        if profile.fire_deaths >= 2:
            early.append(("doors", 2, "past crew lost to fires: stronger doors"))
            notes.append(f"{profile.fire_deaths} crew lost to fires: doors earlier")
        if profile.air_deaths >= 2:
            early.append(("oxygen", 2, "past crew suffocated"))
            notes.append(f"{profile.air_deaths} crew suffocated: oxygen earlier")
        if profile.losses_in_fights >= 2:
            early.append(("engines", 4, "past runs lost fights: more dodge"))
            notes.append(f"lost {profile.losses_in_fights} runs in fights: engines earlier")

    if profile is not None:
        profile.notes = notes
    # Keep the first shield layer upgrade first; put the adapted steps right after it.
    head, tail = steps[:1], steps[1:]
    return head + early + tail[:6] + late + tail[6:]
