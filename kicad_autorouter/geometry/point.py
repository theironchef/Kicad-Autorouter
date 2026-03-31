"""
2D point types for PCB geometry.

IntPoint uses integer coordinates (nanometers) for exact arithmetic.
FloatPoint uses floating-point for intermediate calculations and display.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kicad_autorouter.geometry.vector import FloatVector, IntVector


@dataclass(frozen=True, slots=True)
class IntPoint:
    """Immutable 2D point with integer coordinates (nanometer precision)."""

    x: int
    y: int

    def to_float(self) -> FloatPoint:
        return FloatPoint(float(self.x), float(self.y))

    def translate_by(self, dx: int, dy: int) -> IntPoint:
        return IntPoint(self.x + dx, self.y + dy)

    def difference_by(self, other: IntPoint) -> IntVector:
        from kicad_autorouter.geometry.vector import IntVector
        return IntVector(self.x - other.x, self.y - other.y)

    def distance_to(self, other: IntPoint) -> float:
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)

    def distance_squared(self, other: IntPoint) -> int:
        dx = self.x - other.x
        dy = self.y - other.y
        return dx * dx + dy * dy

    def manhattan_distance(self, other: IntPoint) -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)

    def midpoint(self, other: IntPoint) -> FloatPoint:
        return FloatPoint(
            (self.x + other.x) / 2.0,
            (self.y + other.y) / 2.0,
        )

    def rotate_90_deg(self, count: int, center: IntPoint) -> IntPoint:
        """Rotate by count * 90 degrees around center."""
        dx = self.x - center.x
        dy = self.y - center.y
        count = count % 4
        if count == 1:
            return IntPoint(center.x - dy, center.y + dx)
        elif count == 2:
            return IntPoint(center.x - dx, center.y - dy)
        elif count == 3:
            return IntPoint(center.x + dy, center.y - dx)
        return IntPoint(self.x, self.y)

    def mirror_horizontal(self, axis_x: int) -> IntPoint:
        return IntPoint(2 * axis_x - self.x, self.y)

    def __add__(self, vec: IntVector) -> IntPoint:
        return IntPoint(self.x + vec.x, self.y + vec.y)

    def __sub__(self, other: IntPoint) -> IntVector:
        from kicad_autorouter.geometry.vector import IntVector
        return IntVector(self.x - other.x, self.y - other.y)


@dataclass(frozen=True, slots=True)
class FloatPoint:
    """Immutable 2D point with floating-point coordinates."""

    x: float
    y: float

    def to_int(self) -> IntPoint:
        return IntPoint(round(self.x), round(self.y))

    def translate_by(self, dx: float, dy: float) -> FloatPoint:
        return FloatPoint(self.x + dx, self.y + dy)

    def distance_to(self, other: FloatPoint) -> float:
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)

    def distance_squared(self, other: FloatPoint) -> float:
        dx = self.x - other.x
        dy = self.y - other.y
        return dx * dx + dy * dy

    def rotate(self, angle_rad: float, center: FloatPoint) -> FloatPoint:
        dx = self.x - center.x
        dy = self.y - center.y
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        return FloatPoint(
            center.x + dx * cos_a - dy * sin_a,
            center.y + dx * sin_a + dy * cos_a,
        )

    def midpoint(self, other: FloatPoint) -> FloatPoint:
        return FloatPoint(
            (self.x + other.x) / 2.0,
            (self.y + other.y) / 2.0,
        )

    def __add__(self, vec: FloatVector) -> FloatPoint:
        return FloatPoint(self.x + vec.x, self.y + vec.y)

    def __sub__(self, other: FloatPoint) -> FloatVector:
        from kicad_autorouter.geometry.vector import FloatVector
        return FloatVector(self.x - other.x, self.y - other.y)
