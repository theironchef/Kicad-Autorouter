"""
2D vector types for PCB geometry.

Vectors represent displacements, directions, and offsets.
IntVector uses exact integer arithmetic; FloatVector for intermediate math.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IntVector:
    """Immutable 2D vector with integer components."""

    x: int
    y: int

    def to_float(self) -> FloatVector:
        return FloatVector(float(self.x), float(self.y))

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y)

    def length_squared(self) -> int:
        return self.x * self.x + self.y * self.y

    def dot(self, other: IntVector) -> int:
        return self.x * other.x + self.y * other.y

    def cross(self, other: IntVector) -> int:
        """2D cross product (z-component of 3D cross product)."""
        return self.x * other.y - self.y * other.x

    def negate(self) -> IntVector:
        return IntVector(-self.x, -self.y)

    def scale(self, factor: int) -> IntVector:
        return IntVector(self.x * factor, self.y * factor)

    def rotate_90_deg(self) -> IntVector:
        """Rotate 90 degrees counter-clockwise."""
        return IntVector(-self.y, self.x)

    def is_orthogonal(self) -> bool:
        return self.x == 0 or self.y == 0

    def is_diagonal(self) -> bool:
        return abs(self.x) == abs(self.y) and self.x != 0

    def is_multiple_of_45_deg(self) -> bool:
        return self.is_orthogonal() or self.is_diagonal()

    def side_of(self, other: IntVector) -> int:
        """Return >0 if other is left, <0 if right, 0 if collinear."""
        return self.cross(other)

    def __add__(self, other: IntVector) -> IntVector:
        return IntVector(self.x + other.x, self.y + other.y)

    def __sub__(self, other: IntVector) -> IntVector:
        return IntVector(self.x - other.x, self.y - other.y)

    def __neg__(self) -> IntVector:
        return self.negate()

    def __mul__(self, scalar: int) -> IntVector:
        return self.scale(scalar)

    def __rmul__(self, scalar: int) -> IntVector:
        return self.scale(scalar)


@dataclass(frozen=True, slots=True)
class FloatVector:
    """Immutable 2D vector with floating-point components."""

    x: float
    y: float

    def to_int(self) -> IntVector:
        return IntVector(round(self.x), round(self.y))

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y)

    def length_squared(self) -> float:
        return self.x * self.x + self.y * self.y

    def dot(self, other: FloatVector) -> float:
        return self.x * other.x + self.y * other.y

    def cross(self, other: FloatVector) -> float:
        return self.x * other.y - self.y * other.x

    def normalize(self) -> FloatVector:
        ln = self.length()
        if ln == 0:
            return FloatVector(0.0, 0.0)
        return FloatVector(self.x / ln, self.y / ln)

    def scale(self, factor: float) -> FloatVector:
        return FloatVector(self.x * factor, self.y * factor)

    def rotate(self, angle_rad: float) -> FloatVector:
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        return FloatVector(
            self.x * cos_a - self.y * sin_a,
            self.x * sin_a + self.y * cos_a,
        )

    def negate(self) -> FloatVector:
        return FloatVector(-self.x, -self.y)

    def __add__(self, other: FloatVector) -> FloatVector:
        return FloatVector(self.x + other.x, self.y + other.y)

    def __sub__(self, other: FloatVector) -> FloatVector:
        return FloatVector(self.x - other.x, self.y - other.y)

    def __neg__(self) -> FloatVector:
        return self.negate()

    def __mul__(self, scalar: float) -> FloatVector:
        return self.scale(scalar)

    def __rmul__(self, scalar: float) -> FloatVector:
        return self.scale(scalar)
