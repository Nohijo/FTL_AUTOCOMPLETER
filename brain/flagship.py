"""The Rebel Flagship: what each stage carries, and which of its parts to take out first.

Verified from FTL's data (bosses.xml, blueprints.xml) and code (BossShip::OnLoop):
  - Its guns are separate artillery systems, one per pod room, isolated from the rest of
    the ship (no doors on easy / normal; on hard two pods connect to a crew room):
      ARTILLERY_BOSS_1  burst laser   3 shots x 1 damage, cooldown 20 s
      ARTILLERY_BOSS_2  missiles      3 missiles x 1 damage (ignore shields), cooldown 23 s
      ARTILLERY_BOSS_3  heavy beam    2 damage per room, cooldown 26 s
      ARTILLERY_BOSS_4  ion blast     3 shots of ion (strip shield layers), cooldown 28 s
  - Stage 1: all four pods, 4 shield layers, cloaking, hacking (AE) or a teleporter.
    Stage 2: burst, missiles, beam; drone control (8 power) and a power surge that launches
    a swarm of drones (ShipManager::PrepareSuperDrones).
    Stage 3: burst and missiles; teleporter and mind control; surges are a weapon barrage
    (PrepareSuperBarrage) except every 4th, which raises the Zoltan super shield
    (powerCount % 4 == 3 -> PowerSuperShields).
  - Hitting a pod damages the hull like any room, so going for the pods costs no damage.

pod_order() ranks the live pods by how much they would hurt *this* ship (its shield
layers, evasion, defense drones), with the usual Flagship advice (missiles, then the beam,
then the burst laser, then the ion) as the tie-breaker. targets() adds the systems to hit
once the pods are down.
"""

from dataclasses import dataclass

from observer.memory.reader import ArtilleryInfo, GameSnapshot, RoomInfo, ShipInfo, WeaponInfo

POD_ROLE = {"ARTILLERY_BOSS_1": "burst laser", "ARTILLERY_BOSS_2": "missiles",
            "ARTILLERY_BOSS_3": "beam", "ARTILLERY_BOSS_4": "ion"}
ADVICE_BONUS = {"missiles": 0.3, "beam": 0.2, "burst laser": 0.1, "ion": 0.0}
# After the pods, per stage (system names as in SystemInfo.name).
STAGE_SYSTEMS = {
    1: ["shields", "cloaking", "hacking", "teleporter", "pilot", "engines", "oxygen"],
    2: ["shields", "drones", "pilot", "engines", "oxygen"],
    3: ["shields", "mind", "teleporter", "pilot", "engines", "oxygen"],
}


@dataclass
class Pod:
    role: str
    room: RoomInfo
    info: ArtilleryInfo
    threat: float

    @property
    def label(self) -> str:
        return f"{self.role} pod"


def is_flagship(ship: ShipInfo | None) -> bool:
    return ship is not None and ship.boss_stage > 0


def role_of(weapon: WeaponInfo | None) -> str:
    if weapon is None:
        return "artillery"
    if weapon.blueprint in POD_ROLE:
        return POD_ROLE[weapon.blueprint]
    return {"MISSILES": "missiles", "BEAM": "beam", "BOMB": "bomb"}.get(weapon.weapon_type, "laser")


def enemy_guns(enemy: ShipInfo | None) -> list[tuple[str, WeaponInfo]]:
    """Every enemy weapon that can fire at us: its weapon system's guns and its artillery."""
    if enemy is None:
        return []
    guns = [(w.blueprint, w) for w in enemy.weapons]
    guns += [(f"{role_of(a.weapon)} pod", a.weapon) for a in enemy.artillery
             if a.weapon is not None and a.health > 0]
    return guns


def our_defense(player: ShipInfo) -> tuple[int, float, int]:
    """(shield layers we can count on, chance to dodge, defense drones out)."""
    layers = player.max_shield_layers
    shields = player.systems.get("shields")
    if shields is not None:
        layers = min(layers, min(shields.health, shields.power) // 2)
    engines = player.systems.get("engines")
    helm = player.systems.get("pilot")
    dodge = 0.0
    if engines is not None and helm is not None and engines.power > 0 and (helm.manned or helm.level >= 2):
        dodge = min(0.5, 0.05 * engines.power + (0.05 if helm.manned else 0.0))
    defense = sum(1 for d in player.drones if d.kind.upper() == "DEFENSE" and d.active)
    return layers, dodge, defense


def threat(role: str, player: ShipInfo, ion_alive: bool) -> float:
    """Expected harm of one volley of this pod to our ship (hull points, roughly; system
    damage, fires and breaches folded in)."""
    layers, dodge, defense = our_defense(player)
    if ion_alive and role != "ion":
        layers = max(0, layers - 1)            # the ion pod strips a layer before the others land
    hit = 1.0 - dodge
    if role == "missiles":
        return max(0.0, 3 - defense) * hit + 0.6        # fires / breaches, ignore shields
    if role == "burst laser":
        return max(0, 3 - layers) * hit + 0.3
    if role == "beam":
        return 1.5 * max(0, 2 - layers)                 # beams cannot miss; ~2 rooms crossed
    if role == "ion":
        return 0.8 + 0.4 * (layers <= 2)                # opens the way for every other pod
    return 1.0 * hit


def pods(snapshot: GameSnapshot) -> list[Pod]:
    """The Flagship's live pods, most dangerous first."""
    enemy, player = snapshot.enemy, snapshot.player
    if enemy is None or player is None:
        return []
    rooms = {r.room_id: r for r in enemy.rooms}
    live = [a for a in enemy.artillery if a.health > 0 and a.room_id in rooms]
    ion_alive = any(role_of(a.weapon) == "ion" for a in live)
    found = []
    for art in live:
        role = role_of(art.weapon)
        score = threat(role, player, ion_alive) + ADVICE_BONUS.get(role, 0.0)
        score += 0.4 * (art.level - art.health)     # finish a damaged pod
        found.append(Pod(role, rooms[art.room_id], art, score))
    return sorted(found, key=lambda p: -p.threat)


def targets(snapshot: GameSnapshot, defended: bool, have_launchers: bool,
            drones_aboard: bool = False) -> list[tuple[str, RoomInfo]]:
    """Rooms to aim at, in order: (label, room). Pods first; in stage 2 the drone control
    first when a defense drone is shooting our missiles down, or when its boarding drones
    are aboard (losing the drone control destroys them)."""
    enemy = snapshot.enemy
    stage = enemy.boss_stage
    rooms = {r.room_id: r for r in enemy.rooms}
    order: list[tuple[str, RoomInfo]] = []

    def system(name: str):
        sys = enemy.systems.get(name)
        if sys is not None and sys.health > 0 and sys.room_id in rooms:
            order.append((name, rooms[sys.room_id]))

    if (defended and have_launchers) or drones_aboard:
        system("drones")
    order += [(p.label, p.room) for p in pods(snapshot)]
    for name in STAGE_SYSTEMS.get(stage, STAGE_SYSTEMS[1]):
        system(name)
    if not order and enemy.rooms:
        order.append(("hull", enemy.rooms[0]))
    return order


def surge_kind(enemy: ShipInfo) -> str | None:
    """What the Flagship's next power surge will be."""
    if enemy.boss_stage == 2:
        return "drone swarm"
    if enemy.boss_stage == 3:
        return "Zoltan shield" if enemy.surge_count % 4 == 3 else "weapon barrage"
    return None


def room_alive(enemy: ShipInfo, room_id: int) -> bool:
    """A system (or pod) in this room still works."""
    return (any(s.room_id == room_id and s.health > 0 for s in enemy.systems.values())
            or any(a.room_id == room_id and a.health > 0 for a in enemy.artillery))


def describe(snapshot: GameSnapshot) -> str:
    """One line for the log when a stage starts."""
    enemy = snapshot.enemy
    live = pods(snapshot)
    parts = [f"Flagship stage {enemy.boss_stage}: hull {enemy.hull}/{enemy.max_hull}, "
             f"{enemy.max_shield_layers} shield layers"]
    if live:
        parts.append("pods by threat to us: " + ", ".join(f"{p.role} {p.threat:.1f}" for p in live))
    kind = surge_kind(enemy)
    if kind:
        parts.append(f"next surge: {kind}" + (f" in {enemy.surge_in:.0f}s" if enemy.surge_in is not None else ""))
    return "; ".join(parts)
