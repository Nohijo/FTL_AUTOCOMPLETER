from dataclasses import dataclass, field
from typing import Any


@dataclass
class Action:
    name: str
    priority: int
    reason: str
    data: dict[str, Any] = field(default_factory=dict)

    def __str__(self):
        return (
            f"Action(name={self.name}, "
            f"priority={self.priority}, "
            f"reason={self.reason}, "
            f"data={self.data})"
        )