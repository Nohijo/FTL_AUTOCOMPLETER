"""Brain scenarios replayed from real runs (offline Kestrel, no FTL needed).

    python3 -m unittest tests.test_brain_scenarios

Each test is a situation a run went wrong in, and what the brain must do instead.
"""

import dataclasses
import unittest

from brain.live_combat import LiveCombatBrain
from observer.memory.reader import WeaponInfo
from tests.kestrel import crew, snapshot


def drone(room, health=150.0):
    return dataclasses.replace(crew(f"drone{room}", room, intruder=True), species="battle", name="",
                               health=health, max_health=150.0)


def weapon(name, shots, goal, charge):
    return WeaponInfo(blueprint=name, weapon_type="LASER", powered=True, charge=charge, charge_goal=goal,
                      ready=charge >= goal, required_power=2, damage=1, shots=shots, missiles_per_shot=0,
                      auto_firing=False, targeted=False)


def crew_of(name, room, **kw):
    return crew(name, room, address=abs(hash(name)) & 0xFFFFFF, **kw)


BASE = [crew_of("Ryan", 2, manning=8), crew_of("Pipaluk", 4, manning=5), crew_of("Triko", 5, manning=0),
        crew_of("Jack", 14, manning=1), crew_of("Liu", 3, manning=7), crew_of("Ferry", 0, manning=6)]


def heal(crew_list, health, names=None):
    return [dataclasses.replace(c, health=health) if names is None or c.name in names else c for c in crew_list]


class Game:
    """Runs the brain on a snapshot, applying its crew orders as if they took."""

    def __init__(self, crew_list, rooms=None, systems=None):
        self.snap = snapshot(crew_list)
        for room_id, changes in (rooms or {}).items():
            self.snap.player.rooms = [dataclasses.replace(r, **changes) if r.room_id == room_id else r
                                      for r in self.snap.player.rooms]
        for name, changes in (systems or {}).items():
            self.snap.player.systems[name] = dataclasses.replace(self.snap.player.systems[name], **changes)
        self.brain = LiveCombatBrain()
        self.t = 100.0

    def step(self, seconds=0.3):
        intents = self.brain.decide(self.snap, self.t)
        for it in intents:
            if it.kind == "crew_to_room":
                r = it.room
                self.snap.player.crew = [dataclasses.replace(c, goal_x=r.x + 17, goal_y=r.y + 17, slot_room=r.room_id)
                                         if c.address == it.crew.address else c for c in self.snap.player.crew]
        self.t += seconds
        return intents

    def orders(self, steps=1, seconds=0.3):
        out = []
        for _ in range(steps):
            out += [(it.crew.name, it.room.room_id, it.reason) for it in self.step(seconds) if it.kind == "crew_to_room"]
        return out

    def plans(self):
        return dict(self.brain.boarder_plans)


class Boarders(unittest.TestCase):
    def test_team_goes_together(self):
        # Seen: four crew sent over three seconds, the first one killed 4-on-1.
        g = Game(BASE + [crew_of("B1", 7, intruder=True, health=40), crew_of("B2", 7, intruder=True, health=40)])
        first = g.orders(1)
        self.assertGreaterEqual(sum(1 for _, room, _ in first if room == 7), 2)

    def test_outnumbered_crew_falls_back_early(self):
        # Seen: the door operator alone against two boarders, dead in five seconds.
        g = Game(heal(BASE, 55.0, {"Ryan"}) + [crew_of("B1", 2, intruder=True), crew_of("B2", 2, intruder=True)])
        self.assertIn(("Ryan", 4), [(n, r) for n, r, _ in g.orders(1)])

    def test_no_crew_fed_into_a_losing_fight(self):
        # Seen: an unpowered, wrecked medbay held by three boarders; two crew sent in, both killed.
        g = Game([crew_of("Kozar", 0, manning=6), crew_of("Jones", 0), crew_of("Rafn", 10, manning=3, health=87),
                  crew_of("CYHeng", 12, health=94)]
                 + [crew_of(n, 4, intruder=True) for n in ("S", "B", "A")] + [crew_of("F", 0, intruder=True, health=79)],
                 systems={"medbay": {"health": 0, "power": 0}})
        self.assertNotIn(4, [room for _, room, _ in g.orders(4, 0.5)])

    def test_repair_never_sends_crew_into_boarders(self):
        # Seen: a wrecked medbay's repair outranked the boarders in it.
        g = Game(heal(BASE, 30.0, {"Liu"}) + [crew_of(n, 4, intruder=True) for n in ("S", "B", "A")],
                 systems={"medbay": {"health": 0, "power": 0}})
        for name, room, reason in g.orders(4, 0.5):
            if room == 4:
                self.assertIn("intruder", reason)

    def test_no_medbay_losing_crew_regroup(self):
        g = Game([crew_of("Kozar", 0, manning=6, health=50), crew_of("Jones", 5, manning=0), crew_of("Rafn", 5, health=87)]
                 + [crew_of("B1", 0, intruder=True), crew_of("B2", 0, intruder=True)],
                 systems={"medbay": {"health": 0, "power": 0}})
        moves = [(n, r) for n, r, _ in g.orders(1)]
        self.assertIn(("Kozar", 5), moves)


class BoardingDrones(unittest.TestCase):
    def test_drone_is_never_vented_or_left_to_suffocate(self):
        g = Game(BASE + [drone(3)], rooms={3: {"oxygen": 0.0}})
        g.step()
        self.assertEqual(g.plans()[3], "contain")
        doors = [it for it in g.brain.decide(g.snap, g.t) if it.kind == "door"]
        self.assertEqual(doors, [])

    def test_drone_in_breathable_room_is_engaged_by_a_team(self):
        g = Game(BASE + [drone(10)])
        sent = [r for _, r, _ in g.orders(1) if r == 10]
        self.assertEqual(g.plans()[10], "engage")
        self.assertGreaterEqual(len(sent), 2)

    def test_waiting_on_a_drone_is_time_limited(self):
        g = Game([c for c in BASE if c.name != "Liu"] + [crew_of("Liu", 4), drone(3)], rooms={3: {"oxygen": 0.0}})
        sent = [r for _, r, _ in g.orders(20, 1.0) if r == 3]
        self.assertTrue(sent, "nobody ever went for the drone")

    def test_hurt_crew_heal_instead_of_a_stuck_engage(self):
        g = Game(heal(BASE, 50.0) + [drone(10)])
        orders = g.orders(20, 2.0)
        self.assertEqual(g.plans()[10], "contain")
        self.assertTrue(any(r == 4 for _, r, _ in orders), "nobody went to heal")
        self.assertFalse(any(r == 10 for _, r, _ in orders), "someone walked into the drone alone")


class Fires(unittest.TestCase):
    def test_hurt_crew_are_not_sent_into_fire(self):
        # Seen: crew at half health sent in, recalled a second later, one left alone and burned.
        g = Game(heal(BASE, 52.0, {"Triko", "Jack", "Liu", "Pipaluk"}), rooms={8: {"fires": 4}},
                 systems={"doors": {"health": 0}})
        names = {n for n, r, _ in g.orders(1) if r == 8}
        self.assertFalse(names & {"Triko", "Jack", "Liu", "Pipaluk"})

    def test_big_fire_is_never_fought_alone(self):
        g = Game(heal(BASE, 52.0, {"Ryan", "Pipaluk", "Triko", "Jack", "Liu"}), rooms={8: {"fires": 4}},
                 systems={"doors": {"health": 0}})
        self.assertNotIn(8, [r for _, r, _ in g.orders(3)])


class Breaches(unittest.TestCase):
    def test_airless_breach_gets_patched(self):
        # Seen: the bot waiting forever to jump; nobody allowed into the airless breached room.
        g = Game([crew_of("Kozar", 14, manning=1), crew_of("Jones", 0, manning=6)],
                 rooms={16: {"breaches": 1, "oxygen": 0.0}})
        self.assertIn(16, [r for _, r, _ in g.orders(1)])

    def test_patcher_is_not_recalled_by_the_peacetime_top_up(self):
        g = Game([crew_of("Kozar", 14, manning=1), crew_of("Jones", 0, manning=6)],
                 rooms={16: {"breaches": 1, "oxygen": 0.0}})
        g.orders(1)
        r16 = g.snap.player.rooms[16]
        g.snap.player.crew = [dataclasses.replace(c, health=84.0, room_id=16) if c.name == "Kozar" else c
                              for c in g.snap.player.crew]
        self.assertNotIn(("Kozar", 4), [(n, r) for n, r, _ in g.orders(1)])
        self.assertTrue(r16.breaches)


class Volleys(unittest.TestCase):
    def fight(self, weapons, layers=3):
        snap = snapshot(BASE)
        snap.enemy = dataclasses.replace(snap.player, ship_id=1, is_player=False, hostile=True, targetable_hostile=True,
                                         shield_layers=layers, max_shield_layers=layers, crew=[], blueprint="REBEL_FAT",
                                         jump_charge=0.0, escaping=False)
        snap.enemy_origin = (700, 170)
        snap.player.weapons = weapons
        return [it for it in LiveCombatBrain().decide(snap, 100.0) if it.kind in ("target", "volley")]

    def test_guns_wait_for_each_other(self):
        # Seen: 12 s and 13 s guns aimed early fired a second apart; 3 layers and a defense
        # drone took every shot (Flagship stage 2, its hull untouched for a minute).
        self.assertEqual(self.fight([weapon("LASER_BURST_2", 3, 12, 12), weapon("LASER_HEAVY_2", 2, 13, 12)]), [])

    def test_charged_guns_fire_as_one_volley(self):
        intents = self.fight([weapon("LASER_BURST_2", 3, 12, 12), weapon("LASER_HEAVY_2", 2, 13, 13)])
        self.assertEqual([it.kind for it in intents], ["volley"])
        self.assertEqual(len(intents[0].targets), 2)

    def test_guns_that_get_through_alone_fire_alone(self):
        intents = self.fight([weapon("LASER_BURST_2", 3, 12, 12), weapon("LASER_HEAVY_2", 2, 13, 13)], layers=1)
        self.assertEqual(sorted(it.kind for it in intents), ["target", "target"])


class Strategy(unittest.TestCase):
    """Rules from the strategy reference (FTL_STRATEGY.md)."""

    def test_harsh_sectors_only_for_a_strong_ship(self):
        from brain.navigator import sector_value
        for kind in ("ROCK_SECTOR", "REBEL_SECTOR", "SLUG_SECTOR", "REBEL_SECTOR_MINIBOSS"):
            self.assertLess(sector_value(kind, 4, False), sector_value("CIVILIAN_SECTOR", 4, False), kind)
            self.assertGreaterEqual(sector_value(kind, 4, True), sector_value(kind, 4, False), kind)

    def test_nebula_sector_is_fine_early_and_bad_late(self):
        from brain.navigator import sector_value
        self.assertGreater(sector_value("NEBULA_SECTOR", 2, False), sector_value("NEBULA_SECTOR", 5, False))

    def test_zoltan_cools_off_late(self):
        from brain.navigator import sector_value
        self.assertGreater(sector_value("ZOLTAN_SECTOR", 2, False), sector_value("ZOLTAN_SECTOR", 5, False))

    def test_cloaking_before_the_fourth_shield_layer(self):
        from brain.upgrader import plan_upgrade
        from observer.memory.upgrades import UpgradeOption, UpgradesState
        ship = snapshot(BASE).player
        ship.scrap = 400
        levels = {"shields": 6, "engines": 5, "pilot": 3, "doors": 2, "medbay": 2, "oxygen": 2, "sensors": 2,
                  "weapons": 7, "cloaking": 1}
        options = [UpgradeOption(address=i, system=name, type_id=i, level=level, max_level=8, next_cost=60,
                                 rect=(0, 0, 1, 1), hover=False, pending=0) for i, (name, level) in enumerate(levels.items())]
        state = UpgradesState(open=True, address=0, position=(0, 0), options=options)
        ship.reactor_total = 25
        plan = plan_upgrade(state, ship)
        self.assertIsNotNone(plan)
        self.assertEqual(plan[0], "cloaking")

    def test_early_fuel_is_bought_up_to_25(self):
        import brain.shopper as shopper
        self.assertEqual(shopper.FUEL_TARGET_EARLY, 25)


if __name__ == "__main__":
    unittest.main()
