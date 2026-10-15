from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Resources:
    scrap: Optional[int] = None
    fuel: Optional[int] = None
    missiles: Optional[int] = None
    drones: Optional[int] = None


@dataclass
class ShipState:
    name: Optional[str] = None
    hull: Optional[int] = None
    max_hull: Optional[int] = None
    shields: Optional[int] = None
    oxygen: Optional[int] = None
    reactor_power: Optional[int] = None


@dataclass
class CrewMember:
    name: Optional[str] = None
    race: Optional[str] = None
    hp: Optional[int] = None
    room: Optional[str] = None


@dataclass
class WeaponState:
    name: Optional[str] = None
    powered: Optional[bool] = None
    charge: Optional[float] = None
    required_power: Optional[int] = None


@dataclass
class EnemyState:
    present: bool = False
    hull: Optional[int] = None
    shields: Optional[int] = None


@dataclass
class GameState:
    ship: ShipState = field(default_factory=ShipState)
    resources: Resources = field(default_factory=Resources)
    crew: list[CrewMember] = field(default_factory=list)
    weapons: list[WeaponState] = field(default_factory=list)
    enemy: EnemyState = field(default_factory=EnemyState)
    current_screen: Optional[str] = None