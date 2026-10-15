"""An offline Kestrel (layout read from the live game) for testing the brain without FTL."""

from observer.memory.reader import (CrewInfo, DoorInfo, GameSnapshot, RoomInfo, ShipInfo, SystemInfo)

ROOMS = {0: (490, 140, 35, 70), 1: (420, 140, 70, 70), 2: (350, 140, 70, 35), 3: (350, 175, 70, 35),
         4: (280, 105, 70, 70), 5: (280, 175, 70, 70), 6: (210, 70, 70, 35), 7: (210, 105, 70, 70),
         8: (210, 175, 70, 70), 9: (210, 245, 70, 35), 10: (140, 140, 70, 70), 11: (105, 105, 70, 35),
         12: (105, 210, 70, 35), 13: (35, 105, 70, 35), 14: (35, 140, 70, 70), 15: (35, 210, 70, 35),
         16: (0, 140, 35, 70)}
INTERIOR = [(0, 1), (1, 3), (1, 2), (2, 4), (3, 5), (4, 5), (4, 7), (5, 8), (8, 9), (6, 7), (7, 10), (8, 10),
            (10, 12), (10, 11), (11, 13), (13, 14), (14, 15), (12, 15), (14, 16), (14, 16)]
AIRLOCKS = [16, 16, 9, 9, 6, 6]
SYSTEMS = {"shields": (0, 5), "engines": (1, 14), "oxygen": (2, 13), "weapons": (3, 10), "medbay": (5, 4),
           "pilot": (6, 0), "sensors": (7, 3), "doors": (8, 2)}


def crew(name, room, manning=-1, address=None, intruder=False, health=100.0):
    x, y, w, h = ROOMS[room]
    return CrewInfo(address=address or hash(name) & 0xFFFFFF, name=name, species="human", health=health,
                    max_health=100.0, room_id=room, x=x + 17, y=y + 17, goal_x=x + 17, goal_y=y + 17,
                    slot_room=room, new_path=False, ship_id=0, manning=manning, repairing=False,
                    extinguishing=False, intruder=intruder, fighting=False, dead=False, mind_controlled=False)


def snapshot(crew_list) -> GameSnapshot:
    systems = {name: SystemInfo(type_id=t, name=name, raw_name=name, power=1, level=2, health=2, max_level=8,
                                room_id=r, manned=False, on_fire=False, breached=False, ion_lock=0,
                                battery_power=0, hacked=0) for name, (t, r) in SYSTEMS.items()}
    doors = [DoorInfo(address=0x100 + i, room1=a, room2=b, open=False, x=0, y=0) for i, (a, b) in enumerate(INTERIOR)]
    doors += [DoorInfo(address=0x200 + i, room1=r, room2=-1, open=False, x=0, y=0) for i, r in enumerate(AIRLOCKS)]
    rooms = [RoomInfo(room_id=i, x=x, y=y, w=w, h=h, oxygen=100.0, fires=0, breaches=0) for i, (x, y, w, h) in ROOMS.items()]
    ship = ShipInfo(ship_id=0, blueprint="PLAYER_SHIP_HARD", name="kestral", is_player=True, hull=20, max_hull=30,
                    shield_layers=1, max_shield_layers=1, shield_charge=0.0, super_shield=0, fuel=10, missiles=5,
                    drone_parts=2, scrap=30, jump_charge=85.0, jump_goal=85.0, reactor_used=6, reactor_total=8,
                    oxygen_total=1.0, hostile=False, targetable_hostile=False, destroyed=False, jumping=False,
                    jump_animating=False, surrendered=False, escaping=False, systems=systems, crew=list(crew_list),
                    rooms=rooms, doors=doors)
    return GameSnapshot(screen="in_run", player=ship, player_origin=(350, 170))
