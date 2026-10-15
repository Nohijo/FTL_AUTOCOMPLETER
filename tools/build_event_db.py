"""Build data/events_db.json from FTL's own event files (extracted from ftl.dat).

    python3 tools/build_event_db.py [--extract]

--extract first unpacks the XML files from FTL's ftl.dat (PKG format) into data/ftl_xml.
The database holds every named event, event list (random pick among several events) and
ship (with its destroyed / deadCrew / surrender outcomes), each outcome reduced to the
effects that matter to decisions. brain/event_knowledge.py reads it.
"""

import argparse
import json
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import FTL_DATA_DIR, PROJECT_DIR  # noqa: E402

XML_DIR = PROJECT_DIR / "data" / "ftl_xml"
DB_FILE = PROJECT_DIR / "data" / "events_db.json"
EVENT_FILES = ["events.xml", "events_boss.xml", "events_crystal.xml", "events_engi.xml", "events_fuel.xml",
               "events_mantis.xml", "events_nebula.xml", "events_pirate.xml", "events_rebel.xml", "events_rock.xml",
               "events_ships.xml", "events_slug.xml", "events_zoltan.xml", "dlcEvents.xml", "dlcEvents_anaerobic.xml",
               "dlcEventsOverwrite.xml", "newEvents.xml", "nebula.xml"]


def extract():
    """Unpack data/*.xml from FTL 1.6's ftl.dat (big-endian PKG: header, 20-byte entries, paths)."""
    with open(FTL_DATA_DIR / "ftl.dat", "rb") as f:
        _, header_size, entry_size, count, paths_size = struct.unpack(">4sHHII", f.read(16))
        f.seek(header_size)
        entries = [struct.unpack(">IIIII", f.read(entry_size)[:20]) for _ in range(count)]
        paths = f.read(paths_size)
        for _, flag_off, data_off, size, _ in entries:
            name_off = flag_off & 0xFFFFFF
            name = paths[name_off:paths.index(b"\0", name_off)].decode()
            if not name.startswith("data/") or not name.endswith((".xml", ".xml.append")):
                continue
            f.seek(data_off)
            raw = f.read(size)
            if flag_off >> 24 & 1:
                raw = zlib.decompress(raw)
            path = XML_DIR / name[5:]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)


def load_xml(path: Path) -> ET.Element:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<\?xml[^>]*\?>", "", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = text.replace("&", "&amp;").replace("&amp;amp;", "&amp;")
    return ET.fromstring(f"<root>{text}</root>")


def number(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_outcome(node: ET.Element) -> dict:
    """Effects of one <event> (or <destroyed>/<deadCrew>/<surrender>) node."""
    out: dict = {}
    if node.get("load"):
        out["load"] = node.get("load")
    for child in node:
        tag = child.tag
        if tag == "autoReward":
            out["auto_reward"] = {"level": (child.get("level") or "MEDIUM").upper(), "type": (child.text or "standard").strip()}
        elif tag == "item_modify":
            for item in child.findall("item"):
                low, high = number(item.get("min")), number(item.get("max"))
                out.setdefault("items", {})[item.get("type")] = (low + high) / 2
        elif tag in ("weapon", "drone", "augment"):
            out.setdefault("gear", []).append({"kind": tag, "name": child.get("name")})
        elif tag == "crewMember":
            out["crew"] = out.get("crew", 0) + number(child.get("amount"), 1)
            out["crew_class"] = child.get("class") or child.text or ""
        elif tag == "removeCrew":
            clone = child.find("clone")
            out["remove_crew"] = {"class": child.get("class", ""),
                                  "clone": clone is not None and (clone.text or "").strip() == "true"}
        elif tag == "damage":
            out.setdefault("damage", []).append({"amount": number(child.get("amount")), "system": child.get("system"),
                                                 "effect": child.get("effect")})
        elif tag == "status":
            out.setdefault("status", []).append({"type": child.get("type"), "target": child.get("target"),
                                                 "system": child.get("system"), "amount": number(child.get("amount"))})
        elif tag == "upgrade":
            out.setdefault("upgrade", []).append({"system": child.get("system"), "amount": number(child.get("amount"), 1)})
        elif tag == "system":
            out["system"] = child.get("name")
        elif tag == "boarders":
            out["boarders"] = {"min": number(child.get("min")), "max": number(child.get("max")), "class": child.get("class")}
        elif tag == "ship":
            out["ship"] = {"load": child.get("load"), "hostile": (child.get("hostile") or "false") == "true"}
        elif tag in ("store", "repair", "reveal_map", "secretSector", "distressBeacon"):
            out[tag] = True
        elif tag == "modifyPursuit":
            out["pursuit"] = number(child.get("amount"))
        elif tag == "quest":
            out["quest"] = child.get("event") or True
        elif tag == "unlockShip":
            out["unlock_ship"] = child.get("id") or True
        elif tag == "environment":
            out["environment"] = child.get("type")
        elif tag == "choice":
            out.setdefault("choices", []).append(parse_choice(child))
    return out


def parse_choice(node: ET.Element) -> dict:
    choice = {"req": node.get("req"), "lvl": number(node.get("lvl")), "max_lvl": number(node.get("max_lvl"), 0),
              "blue": node.get("blue") != "false", "hidden": node.get("hidden") == "true"}
    text = node.find("text")
    if text is not None:
        choice["text_id"] = text.get("id") or (text.text or "").strip()[:80]
    event = node.find("event")
    choice["event"] = parse_outcome(event) if event is not None else {}
    return choice


def build() -> dict:
    db = {"events": {}, "lists": {}, "ships": {}}
    for name in EVENT_FILES:
        path = XML_DIR / name
        if not path.exists():
            continue
        try:
            root = load_xml(path)
        except ET.ParseError as error:
            print(f"[events_db] skipped {name}: {error}")
            continue
        nodes = [n for top in root for n in (top if top.tag == "FTL" else [top])]   # files wrap in <FTL>
        for node in nodes:
            key = node.get("name")
            if not key:
                continue
            if node.tag == "event":
                db["events"][key] = parse_outcome(node) | {"file": name, "unique": node.get("unique") == "true"}
            elif node.tag == "eventList":
                db["lists"][key] = [parse_outcome(e) for e in node.findall("event")]
            elif node.tag == "ship":
                ship = {"auto_blueprint": node.get("auto_blueprint")}
                for part in ("destroyed", "deadCrew", "surrender", "escape", "gotaway"):
                    child = node.find(part)
                    if child is not None:
                        ship[part] = parse_outcome(child)
                db["ships"][key] = ship
    return db


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extract", action="store_true", help="unpack the XML from ftl.dat first")
    args = parser.parse_args()
    if args.extract or not XML_DIR.exists():
        extract()
    db = build()
    DB_FILE.write_text(json.dumps(db, indent=1))
    print(f"[events_db] {len(db['events'])} events, {len(db['lists'])} event lists, {len(db['ships'])} ships -> {DB_FILE}")


if __name__ == "__main__":
    main()
