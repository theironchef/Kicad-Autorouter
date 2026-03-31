"""
Line and line segment types for PCB geometry.

Lines are infinite; LineSegments are bounded by two endpoints.
Used extensively in trace routing and shape intersection calculations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from kicad_autorouter.geometry.point import FloatPoint, IntPoint
from kicad_autorouter.geometry.side import Side
from kicad_autorouter.geometry.vector import IntVector


@dataclass(frozen=True, slots=True)
class Line:
    """Infinite line defined by a point and direction vector.

    Represented in the form: a*x + b*y + c = 0
    where (a, b) is the normal vector.
    """

    a: int
    b: int
    c: int

    @staticmethod
    def from_two_points(p1: IntPoint, p2: IntPoint) -> Line:
        """Create a line passing through two points."""
        a = p1.y - p2.y
        b = p2.x - p1.x
        c = -(a * p1.x + b * p1.y)
        return Line(a, b, c)

    @staticmethod
    def from_point_and_direction(p: IntPoint, direction: IntVector) -> Line:
        a = -direction.y
        b = direction.x
        c = -(a * p.x + b * p.y)
        return Line(a, b, c)

    def side_of(self, p: IntPoint) -> Side:
        """Determine which side of this line a point lies on."""
        val = self.a * p.x + self.b * p.y + self.c
        return Side.of(val)

    def signed_distance(self, p: IntPoint) -> float:
        """Signed distance from point to line (positive = left side)."""
        num = self.a * p.x + self.b * p.y + self.c
        denom = math.sqrt(self.a * self.a + self.b * self.b)
        if denom == 0:
            return 0.0
        return num / denom

    def distance(self, p: IntPoint) -> float:
        """Unsigned distance from point to line."""
        return abs(self.signed_distance(p))

    def direction(self) -> IntVector:
        """Direction vector along this line."""
        return IntVector(self.b, -self.a)

    def normal(self) -> IntVector:
        """Normal vector (pointing to the left side)."""
        return IntVector(self.a, self.b)

    def intersection_with(self, other: Line) -> FloatPoint | None:
        """Find intersection point with another line. Returns None if parallel."""
        det = self.a * other.b - other.a * self.b
        if det == 0:
            return None
        x = (self.b * other.c - other.b * self.c) / det
        y = (other.a * self.c - self.a * other.c) / det
        return FloatPoint(x, y)

    def perpendicular_through(self, p: IntPoint) -> Line:
        """Line perpendicular to this one passing through p."""
        # Normal of perpendicular is (b, -a), i.e., the direction of this line
        new_a = self.b
        new_b = -self.a
        new_c = -(new_a * p.x + new_b * p.y)
        return Line(new_a, new_b, new_c)

    def is_parallel_to(self, other: Line) -> bool:
        return self.a * other.b == other.a * self.b

    def translate(self, dx: int, dy: int) -> Line:
        return Line(self.a, self.b, self.c - self.a * dx - self.b * dy)


@dataclass(frozen=True, slots=True)
class LineSegment:
    """Bounded line segment between two integer points."""

    start: IntPoint
    end: IntPoint

    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)

    @property
    def length_squared(self) -> int:
        return self.start.distance_squared(self.end)

    @property
    def midpoint(self) -> FloatPoint:
        return self.start.midpoint(self.end)

    def to_line(self) -> Line:
        return Line.from_two_points(self.start, self.end)

    def direction(self) -> IntVector:
        return self.start.difference_by(self.end).negate()

    def closest_point(self, p: IntPoint) -> FloatPoint:
        """Find the closest point on this segment to point p."""
        dx = self.end.x - self.start.x
        dy = self.end.y - self.start.y
        len_sq = dx * dx + dy * dy
        if len_sq == 0:
            return self.start.to_float()

        t = ((p.x - self.start.x) * dx + (p.y - self.start.y) * dy) / len_sq
        t = max(0.0, min(1.0, t))
        return FloatPoint(
            self.start.x + t * dx,
            self.start.y + t * dy,
        )

    def distance_to_point(self, p: IntPoint) -> float:
        closest = self.closest_point(p)
        fp = p.to_float()
        return fp.distance_to(closest)

    def intersects_segment(self, other: LineSegment) -> bool:
        """Check if this segment intersects another segment."""
        d1 = _cross(self.start, self.end, other.start)
        d2 = _cross(self.start, self.end, other.end)
        d3 = _cross(other.start, other.end, self.start)
        d4 = _cross(other.start, other.end, self.end)

        if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
           ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
            return True

        if d1 == 0 and _on_segment(self.start, self.end, other.start):
            return True
        if d2 == 0 and _on_segment(self.start, self.end, other.end):
            return True
        if d3 == 0 and _on_segment(other.start, other.end, self.start):
            return True
        if d4 == 0 and _on_segment(other.start, other.end, self.end):
            return True

        return False

    def intersection_point(self, other: LineSegment) -> FloatPoint | None:
        """Find intersection point with another segment, or None."""
        line1 = self.to_line()
        line2 = other.to_line()
        pt = line1.intersection_with(line2)
        if pt is None:
            return None

        # Check if intersection is within both segments
        if (min(self.start.x, self.end.x) - 1 <= pt.x <= max(self.start.x, self.end.x) + 1 and
            min(self.start.y, self.end.y) - 1 <= pt.y <= max(self.start.y, self.end.y) + 1 and
            min(other.start.x, other.end.x) - 1 <= pt.x <= max(other.start.x, other.end.x) + 1 and
            min(other.start.y, other.end.y) - 1 <= pt.y <= max(other.start.y, other.end.y) + 1):
            return pt
        return None


def _cross(o: IntPoint, a: IntPoint, b: IntPoint) -> int:
    """Cross product of vectors OA and OB."""
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)


def _on_segment(p: IntPoint, q: IntPoint, r: IntPoint) -> bool:
    """Check if point r lies on segment pq (given collinearity)."""
    return (min(p.x, q.x) <= r.x <= max(p.x, q.x) and
            min(p.y, q.y) <= r.y <= max(p.y, q.y))
