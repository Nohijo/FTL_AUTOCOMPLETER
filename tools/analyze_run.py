"""Summarise one run's mistakes from its log (data/runs/<id>/events.jsonl).

    python3 tools/analyze_run.py            the latest run
    python3 tools/analyze_run.py <run id>   a given run (folder name in data/runs)

Prints how the run ended, every fight (time, hull lost, hits with shields down, failed
actions), crew deaths with their causes, failure rates per kind of action, repeated
identical actions (loops), and supplies bought, so runs can be compared before and after
a fix.
"""

import collections
import json
import re
import sys
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "data" / "runs"
ACTIONS = {"crew_to_room", "target", "power_system", "depower_system", "door", "doors_all", "power_weapon",
           "unpower_weapon", "power_drone", "unpower_drone", "autofire", "cloak", "system_target", "system_key",
           "lockdown", "event", "jump", "volley"}       # lines that report an action and its result after " -> "


def failed(text: str) -> str | None:
    """The failure message of an action line, or None when it worked (or is not an action)."""
    match = re.match(r"\[bot\] (?:\(mid-fight\) )?([a-z_]+): (.*)$", text)
    if not match or " -> " not in text:
        return None
    kind, rest = match.groups()
    if kind not in ACTIONS:
        return None
    if kind == "target" and rest.count(" -> ") < 2:
        return None                           # "weapon -> enemy room" with no result: it worked
    result = text.rsplit(" -> ", 1)[1]
    return None if result.startswith(("ok", "stale")) else result   # stale: the fight ended first


def main():
    runs = sorted(p for p in RUNS.glob("2026*") if (p / "events.jsonl").exists())
    run = RUNS / sys.argv[1] if len(sys.argv) > 1 else runs[-1]
    rows = [json.loads(line) for line in open(run / "events.jsonl")]
    summary_file = run / "summary.json"
    summary = json.loads(summary_file.read_text()) if summary_file.exists() else {}
    print(f"RUN {run.name}: outcome {summary.get('run', {}).get('outcome')}, sector {summary.get('sector_reached')}")

    fights = [r for r in rows if r["kind"] == "combat_end"]
    print(f"\nFIGHTS ({len(fights)})")
    for f in fights:
        print(f"  {f['t'][11:19]} {f['enemy']:<20} {f['result']:<13} {f['seconds']:5.0f}s  hull -{f.get('hull_lost', 0):<3}"
              f" hits {f.get('hits_taken', 0):2} ({f.get('hits_with_shields_down', 0)} shields down)"
              f"  aims {f.get('aims', 0):3}  failed actions {f.get('failed_actions', 0)}")

    deaths = [r for r in rows if r["kind"] in ("crew_died", "crew_death")]
    print(f"\nCREW DEATHS ({len(deaths)})")
    for d in deaths:
        print(f"  {d['t'][11:19]} {d['name']} ({d['species']}): {d['cause']}")

    lines = [r["text"] for r in rows if r["kind"] == "bot"]
    totals, fails = collections.Counter(), collections.Counter()
    for text in lines:
        match = re.match(r"\[bot\] (?:\(mid-fight\) )?([a-z_]+): ", text)
        if match and " -> " in text and match.group(1) in ACTIONS:
            totals[match.group(1)] += 1
            message = failed(text)
            if message:
                fails[(match.group(1), re.sub(r"\d+", "N", message)[:70])] += 1
    print("\nFAILURE RATES")
    for kind, total in totals.most_common():
        bad = sum(n for (k, _), n in fails.items() if k == kind)
        if bad:
            print(f"  {kind:<16} {bad:4}/{total:<5} ({bad / total:.0%})")
    print("\nMOST COMMON FAILURES")
    for (kind, message), n in fails.most_common(12):
        print(f"  {n:4} {kind:<16} {message}")

    print("\nLOOPS (the same action 4+ times in a row)")
    last, streak, shown = None, 0, 0
    for text in lines:
        if "store: bought" in text:
            continue                          # repairs are bought one point at a time
        key = re.sub(r"\d+", "N", text)
        streak = streak + 1 if key == last else 1
        last = key
        if streak == 4 and shown < 10:
            print(f"  {text[:150]}")
            shown += 1

    bought = collections.Counter(m.group(1) for t in lines if (m := re.search(r"store: bought (\S+)", t)))
    print(f"\nBOUGHT {dict(bought)}")


if __name__ == "__main__":
    main()
