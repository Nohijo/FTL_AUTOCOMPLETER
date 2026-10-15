"""Compile every failed run into one report: data/runs/POSTMORTEMS.md.

    python3 postmortem.py            write the report and print where it is
    python3 postmortem.py --all      include runs that did not fail (quit, in progress)

Written for Claude: per run, the outcome and its cause, what went well and badly, the
fatal fight and the last notable events, then patterns across runs (causes, ships,
recurring failures). The raw data for a run is in its directory (see data/runs/README.md).
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from observer.run_log import INDEX_FILE, RUNS_DIR

REPORT = RUNS_DIR / "POSTMORTEMS.md"
FAILED = {"destroyed", "crew_lost", "game_over", "base_destroyed"}
NOTABLE = {"combat_start", "combat_end", "hull_damage", "system_damage", "crew_died", "boarded", "boarders_gone",
           "fire", "breach", "oxygen_low", "bot_status", "run_end", "arrived"}


def load_runs() -> list[dict]:
    runs = []
    for directory in sorted(p for p in RUNS_DIR.iterdir() if p.is_dir()) if RUNS_DIR.exists() else []:
        summary_file = directory / "summary.json"
        if summary_file.exists():
            summary = json.loads(summary_file.read_text())
            summary["dir"] = str(directory)
            runs.append(summary)
    return runs


def describe(summary: dict) -> list[str]:
    run = summary["run"]
    lines = [f"## {run['id']}", "",
             f"- ship: {run.get('blueprint')} \"{run.get('ship_name')}\"; started sector {run.get('start_sector')}"
             f"{' (bot attached mid-run)' if run.get('attached_mid_run') else ''}; reached sector {summary.get('sector_reached')}",
             f"- outcome: **{run.get('outcome', 'in progress')}** - {run.get('cause', '')}",
             f"- start loadout: weapons {run.get('start_weapons')}, drones {run.get('start_drones')}, "
             f"crew {[c[1] for c in run.get('start_crew', [])]}",
             f"- data: {summary['dir']}"]
    if summary.get("good"):
        lines += ["", "Went well:"] + [f"- {g}" for g in summary["good"]]
    if summary.get("bad"):
        lines += ["", "Went badly:"] + [f"- {b}" for b in summary["bad"]]
    fights = summary.get("stats", {}).get("fights", [])
    if fights:
        last = fights[-1]
        lines += ["", f"Last fight: {json.dumps(last)}"]
    events = [e for e in summary.get("last_events", []) if e["kind"] in NOTABLE][-30:]
    if events:
        lines += ["", "Last notable events:", "```"]
        for e in events:
            detail = {k: v for k, v in e.items() if k not in ("t", "kind", "run_s")}
            lines.append(f"{e['t'][11:19]} {e['kind']:<14} {json.dumps(detail, default=str)[:220]}")
        lines.append("```")
    failures = summary.get("stats", {}).get("failure_examples", [])
    if failures:
        lines += ["", "Bot action failures (first ones):"] + [f"- {f}" for f in failures[:10]]
    return lines + [""]


def patterns(runs: list[dict]) -> list[str]:
    outcomes = Counter(r["run"].get("outcome", "in progress") for r in runs)
    causes = Counter(r["run"].get("cause", "")[:60] for r in runs if r["run"].get("outcome") in FAILED)
    ships = Counter(r["run"].get("blueprint") for r in runs)
    killers = Counter(f["enemy"] for r in runs for f in r.get("stats", {}).get("fights", []) if f["result"] == "we_lost")
    deaths = Counter(d["cause"].split(" (")[0] for r in runs for d in r.get("stats", {}).get("crew_deaths", []))
    failure_kinds = Counter(f.split(" -> ")[-1][:60] for r in runs for f in r.get("stats", {}).get("failure_examples", []))
    sectors = [r.get("sector_reached") or 0 for r in runs]
    lines = ["## Patterns across runs", "",
             f"- runs: {len(runs)}; outcomes: {dict(outcomes)}; average sector reached: "
             f"{sum(sectors) / len(sectors):.1f}" if sectors else "- no runs",
             f"- ships: {dict(ships)}",
             f"- causes of failed runs: {dict(causes)}",
             f"- enemies that destroyed us: {dict(killers)}",
             f"- crew death causes: {dict(deaths)}",
             f"- most common action failures: {dict(failure_kinds.most_common(8))}", ""]
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="include runs that did not fail")
    args = parser.parse_args()
    runs = load_runs()
    chosen = runs if args.all else [r for r in runs if r["run"].get("outcome") in FAILED]
    lines = ["# FTL bot post-mortems", "",
             f"{len(chosen)} {'runs' if args.all else 'failed runs'} of {len(runs)} recorded "
             f"(index: {INDEX_FILE}). Format: data/runs/README.md.", ""]
    lines += patterns(chosen) if chosen else ["No failed runs recorded yet.", ""]
    for summary in reversed(chosen):          # newest first
        lines += describe(summary)
    notes = RUNS_DIR / "pre_recorder_notes.md"
    if notes.exists():
        lines += ["", notes.read_text()]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines))
    print(f"[postmortem] {len(chosen)} run(s) -> {REPORT}")


if __name__ == "__main__":
    main()
