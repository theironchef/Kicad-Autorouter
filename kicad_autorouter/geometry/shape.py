"""
Abstract shape hierarchy for PCB geometry.

Shape           - Base interface for all 2D shapes
ConvexShape     - Shape guaranteed to be convex (enables fast intersection)
TileShape       - ConvexShape used as spatial tiles in the expansion room system

The autorouter partitions free space into TileShapes for maze search.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from kicad_autorouter.geometry.point import FloatPoint, IntPoint


class Shape(ABC):
    """Abstract base for all 2D shapes."""

    @abstractmethod
    def bounding_box(self) -> BoundingBox:
        """Axis-aligned bounding box."""
        ...

    @abstractmethod
    def contains(self, point: IntPoint) -> bool:
        """Test if a point is inside this shape."""
        ...

    @abstractmethod
    def translate(self, dx: int, dy: int) -> Shape:
        """Return a translated copy."""
        ...

    @abstractmethod
    def area(self) -> float:
        """Area of the shape."""
        ...

    def overlaps(self, other: Shape) -> bool:
        """Conservative overlap test via bounding boxes."""
        return self.bounding_box().intersects(other.bounding_box())


class ConvexShape(Shape, ABC):
    """A convex 2D shape.

    Convexity enables efficient intersection and containment tests.
    All expansion room tile shapes must be convex.
    """

    @abstractmethod
    def contains_inside(self, point: IntPoint) -> bool:
        """Strict interior containment (not on boundary)."""
        ...

    @abstractmethod
    def intersection(self, other: ConvexShape) -> ConvexShape | None:
        """Intersection with another convex shape, or None if disjoint."""
        ...

    @abstractmethod
    def enlarge(self, offset: int) -> ConvexShape:
        """Minkowski sum with a square of given half-width (for clearance)."""
        ...

    @abstractmethod
    def corner_count(self) -> int:
        """Number of vertices."""
        ...

    @abstractmethod
    def corner(self, index: int) -> IntPoint:
        """Get vertex at index."""
        ...

    @abstractmethod
    def edge_line(self, index: int) -> tuple[IntPoint, IntPoint]:
        """Get edge as (start, end) point pair."""
        ...


class TileShape(ConvexShape, ABC):
    """Convex tile used in the autorouter's spatial partitioning.

    The expansion room system divides PCB free space into non-overlapping
    TileShapes. The maze search algorithm expands through doors between
    adjacent tiles.
    """

    @abstractmethod
    def split_by_line(self, line_a: int, line_b: int, line_c: int) -> tuple[TileShape | None, TileShape | None]:
        """Split this tile by line ax + by + c = 0.

        Returns (left_piece, right_piece), either may be None.
        """
        ...

    @abstractmethod
    def center(self) -> FloatPoint:
        """Centroid of the tile."""
        ...

    @abstractmethod
    def max_width(self) -> float:
        """Maximum dimension (for cost estimation)."""
        ...


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Axis-aligned bounding box."""

    x_min: int
    y_min: int
    x_max: int
    y_max: int

    @staticmethod
    def from_points(points: list[IntPoint]) -> BoundingBox:
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        return BoundingBox(min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def from_center_and_radius(center: IntPoint, radius: int) -> BoundingBox:
        return BoundingBox(
            center.x - radius, center.y - radius,
            center.x + radius, center.y + radius,
        )

    @property
    def width(self) -> int:
        return self.x_max - self.x_min

    @property
    def height(self) -> int:
        return self.y_max - self.y_min

    def center(self) -> FloatPoint:
        return FloatPoint(
            (self.x_min + self.x_max) / 2.0,
            (self.y_min + self.y_max) / 2.0,
        )

    def contains(self, p: IntPoint) -> bool:
        return self.x_min <= p.x <= self.x_max and self.y_min <= p.y <= self.y_max

    def contains_box(self, other: BoundingBox) -> bool:
        return (self.x_min <= other.x_min and self.x_max >= other.x_max and
                self.y_min <= other.y_min and self.y_max >= other.y_max)

    def intersects(self, other: BoundingBox) -> bool:
        return not (self.x_max < other.x_min or other.x_max < self.x_min or
                    self.y_max < other.y_min or other.y_max < self.y_min)

    def union(self, other: BoundingBox) -> BoundingBox:
        return BoundingBox(
            min(self.x_min, other.x_min),
            min(self.y_min, other.y_min),
            max(self.x_max, other.x_max),
            max(self.y_max, other.y_max),
        )

    def intersection(self, other: BoundingBox) -> BoundingBox | None:
        x_min = max(self.x_min, other.x_min)
        y_min = max(self.y_min, other.y_min)
        x_max = min(self.x_max, other.x_max)
        y_max = min(self.y_max, other.y_max)
        if x_min > x_max or y_min > y_max:
            return None
        return BoundingBox(x_min, y_min, x_max, y_max)

    def enlarge(self, margin: int) -> BoundingBox:
        return BoundingBox(
            self.x_min - margin, self.y_min - margin,
            self.x_max + margin, self.y_max + margin,
        )

    def area(self) -> int:
        return self.width * self.height
