"""
Direction types for PCB routing.

Direction represents an angle/heading. Direction45 enumerates the 8 cardinal
and intercardinal directions used in 45-degree routing mode.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

from kicad_autorouter.geometry.vector import IntVector


class Direction45(IntEnum):
    """The eight 45-degree-increment directions used in PCB routing."""

    RIGHT = 0       # +X
    UP_RIGHT = 1    # +X, -Y  (screen coords: Y increases downward)
    UP = 2          # -Y
    UP_LEFT = 3     # -X, -Y
    LEFT = 4        # -X
    DOWN_LEFT = 5   # -X, +Y
    DOWN = 6        # +Y
    DOWN_RIGHT = 7  # +X, +Y

    @property
    def dx(self) -> int:
        return _DIR45_DX[self.value]

    @property
    def dy(self) -> int:
        return _DIR45_DY[self.value]

    def to_vector(self, length: int = 1) -> IntVector:
        return IntVector(self.dx * length, self.dy * length)

    def opposite(self) -> Direction45:
        return Direction45((self.value + 4) % 8)

    def rotate_45(self, steps: int = 1) -> Direction45:
        return Direction45((self.value + steps) % 8)

    def is_orthogonal(self) -> bool:
        return self.value % 2 == 0

    def is_diagonal(self) -> bool:
        return self.value % 2 == 1


# Lookup tables for direction deltas
_DIR45_DX = [1, 1, 0, -1, -1, -1, 0, 1]
_DIR45_DY = [0, -1, -1, -1, 0, 1, 1, 1]


@dataclass(frozen=True, slots=True)
class Direction:
    """General direction defined by a unit vector angle."""

    angle_rad: float  # Radians, 0 = right, counter-clockwise positive

    @staticmethod
    def from_vector(dx: float, dy: float) -> Direction:
        return Direction(math.atan2(-dy, dx))  # Negate dy for screen coords

    @staticmethod
    def from_points(x1: float, y1: float, x2: float, y2: float) -> Direction:
        return Direction.from_vector(x2 - x1, y2 - y1)

    @staticmethod
    def from_45(d: Direction45) -> Direction:
        return Direction.from_vector(float(d.dx), float(d.dy))

    def to_nearest_45(self) -> Direction45:
        """Snap to the nearest 45-degree direction."""
        # Normalize to [0, 2*pi)
        a = self.angle_rad % (2 * math.pi)
        index = round(a / (math.pi / 4)) % 8
        return Direction45(index)

    def opposite(self) -> Direction:
        return Direction(self.angle_rad + math.pi)

    def angle_degrees(self) -> float:
        return math.degrees(self.angle_rad)

    def unit_vector(self) -> tuple[float, float]:
        return (math.cos(self.angle_rad), -math.sin(self.angle_rad))
