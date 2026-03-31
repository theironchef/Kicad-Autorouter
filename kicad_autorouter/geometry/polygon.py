"""
Polygon and polyline types for PCB geometry.

Polygon     - Closed shape defined by ordered vertices
Polyline    - Open path defined by ordered corner points (used for traces)
PolygonShape - Convex polygon implementing the TileShape interface
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kicad_autorouter.geometry.line import LineSegment
from kicad_autorouter.geometry.point import FloatPoint, IntPoint
from kicad_autorouter.geometry.shape import BoundingBox, ConvexShape, TileShape
from kicad_autorouter.geometry.side import Side
from kicad_autorouter.geometry.vector import IntVector


@dataclass(frozen=True)
class Polyline:
    """Open path through a sequence of corner points.

    Used to represent trace paths in the routing board.
    """

    corners: tuple[IntPoint, ...]

    def __post_init__(self):
        if len(self.corners) < 2:
            raise ValueError("Polyline requires at least 2 corners")

    @property
    def segment_count(self) -> int:
        return len(self.corners) - 1

    def segment(self, index: int) -> LineSegment:
        return LineSegment(self.corners[index], self.corners[index + 1])

    def segments(self):
        for i in range(self.segment_count):
            yield LineSegment(self.corners[i], self.corners[i + 1])

    def total_length(self) -> float:
        return sum(seg.length for seg in self.segments())

    def reverse(self) -> Polyline:
        return Polyline(tuple(reversed(self.corners)))

    def bounding_box(self) -> BoundingBox:
        return BoundingBox.from_points(list(self.corners))

    def append(self, point: IntPoint) -> Polyline:
        return Polyline(self.corners + (point,))

    def translate(self, dx: int, dy: int) -> Polyline:
        return Polyline(tuple(p.translate_by(dx, dy) for p in self.corners))

    @property
    def first(self) -> IntPoint:
        return self.corners[0]

    @property
    def last(self) -> IntPoint:
        return self.corners[-1]


@dataclass(frozen=True)
class Polygon:
    """Closed polygon defined by ordered vertices (CCW winding)."""

    vertices: tuple[IntPoint, ...]

    def __post_init__(self):
        if len(self.vertices) < 3:
            raise ValueError("Polygon requires at least 3 vertices")

    @property
    def edge_count(self) -> int:
        return len(self.vertices)

    def edge(self, index: int) -> LineSegment:
        n = len(self.vertices)
        return LineSegment(self.vertices[index % n], self.vertices[(index + 1) % n])

    def edges(self):
        n = len(self.vertices)
        for i in range(n):
            yield LineSegment(self.vertices[i], self.vertices[(i + 1) % n])

    def signed_area(self) -> float:
        """Signed area (positive if CCW, negative if CW)."""
        total = 0
        n = len(self.vertices)
        for i in range(n):
            j = (i + 1) % n
            total += self.vertices[i].x * self.vertices[j].y
            total -= self.vertices[j].x * self.vertices[i].y
        return total / 2.0

    def area(self) -> float:
        return abs(self.signed_area())

    def centroid(self) -> FloatPoint:
        n = len(self.vertices)
        cx, cy = 0.0, 0.0
        a = self.signed_area()
        if abs(a) < 1e-10:
            # Degenerate: return average
            for v in self.vertices:
                cx += v.x
                cy += v.y
            return FloatPoint(cx / n, cy / n)

        for i in range(n):
            j = (i + 1) % n
            cross = (self.vertices[i].x * self.vertices[j].y -
                     self.vertices[j].x * self.vertices[i].y)
            cx += (self.vertices[i].x + self.vertices[j].x) * cross
            cy += (self.vertices[i].y + self.vertices[j].y) * cross
        factor = 1.0 / (6.0 * a)
        return FloatPoint(cx * factor, cy * factor)

    def contains(self, p: IntPoint) -> bool:
        """Ray-casting point-in-polygon test."""
        n = len(self.vertices)
        inside = False
        j = n - 1
        for i in range(n):
            vi = self.vertices[i]
            vj = self.vertices[j]
            if ((vi.y > p.y) != (vj.y > p.y) and
                    p.x < (vj.x - vi.x) * (p.y - vi.y) / (vj.y - vi.y) + vi.x):
                inside = not inside
            j = i
        return inside

    def bounding_box(self) -> BoundingBox:
        return BoundingBox.from_points(list(self.vertices))

    def translate(self, dx: int, dy: int) -> Polygon:
        return Polygon(tuple(p.translate_by(dx, dy) for p in self.vertices))

    def is_convex(self) -> bool:
        n = len(self.vertices)
        if n < 3:
            return False
        sign = None
        for i in range(n):
            a = self.vertices[i]
            b = self.vertices[(i + 1) % n]
            c = self.vertices[(i + 2) % n]
            cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x)
            if cross != 0:
                if sign is None:
                    sign = cross > 0
                elif (cross > 0) != sign:
                    return False
        return True


@dataclass(frozen=True)
class PolygonShape(TileShape):
    """Convex polygon implementing TileShape for the expansion room system.

    Vertices must be in counter-clockwise order and the polygon must be convex.
    """

    vertices: tuple[IntPoint, ...]

    def __post_init__(self):
        if len(self.vertices) < 3:
            raise ValueError("PolygonShape requires at least 3 vertices")

    # -- Shape interface --

    def bounding_box(self) -> BoundingBox:
        return BoundingBox.from_points(list(self.vertices))

    def contains(self, point: IntPoint) -> bool:
        # For convex polygon: point must be on the same side of all edges
        n = len(self.vertices)
        for i in range(n):
            j = (i + 1) % n
            cross = ((self.vertices[j].x - self.vertices[i].x) * (point.y - self.vertices[i].y) -
                     (self.vertices[j].y - self.vertices[i].y) * (point.x - self.vertices[i].x))
            if cross < 0:
                return False
        return True

    def translate(self, dx: int, dy: int) -> PolygonShape:
        return PolygonShape(tuple(p.translate_by(dx, dy) for p in self.vertices))

    def area(self) -> float:
        total = 0
        n = len(self.vertices)
        for i in range(n):
            j = (i + 1) % n
            total += self.vertices[i].x * self.vertices[j].y
            total -= self.vertices[j].x * self.vertices[i].y
        return abs(total) / 2.0

    # -- ConvexShape interface --

    def contains_inside(self, point: IntPoint) -> bool:
        n = len(self.vertices)
        for i in range(n):
            j = (i + 1) % n
            cross = ((self.vertices[j].x - self.vertices[i].x) * (point.y - self.vertices[i].y) -
                     (self.vertices[j].y - self.vertices[i].y) * (point.x - self.vertices[i].x))
            if cross <= 0:
                return False
        return True

    def intersection(self, other: ConvexShape) -> ConvexShape | None:
        # Sutherland-Hodgman clipping for convex-convex intersection
        if not isinstance(other, PolygonShape):
            return None  # Only polygon-polygon for now

        output = list(other.vertices)
        n = len(self.vertices)

        for i in range(n):
            if not output:
                return None
            input_list = output
            output = []
            edge_start = self.vertices[i]
            edge_end = self.vertices[(i + 1) % n]

            for j in range(len(input_list)):
                current = input_list[j]
                prev = input_list[j - 1]

                curr_side = _edge_side(edge_start, edge_end, current)
                prev_side = _edge_side(edge_start, edge_end, prev)

                if curr_side >= 0:
                    if prev_side < 0:
                        ix = _edge_intersection(edge_start, edge_end, prev, current)
                        if ix:
                            output.append(ix)
                    output.append(current)
                elif prev_side >= 0:
                    ix = _edge_intersection(edge_start, edge_end, prev, current)
                    if ix:
                        output.append(ix)

        if len(output) < 3:
            return None
        return PolygonShape(tuple(output))

    def enlarge(self, offset: int) -> PolygonShape:
        """Offset polygon outward by moving each edge by offset distance."""
        n = len(self.vertices)
        new_vertices = []
        for i in range(n):
            prev_i = (i - 1) % n
            next_i = (i + 1) % n

            # Edge normals (outward for CCW polygon)
            e1 = IntVector(
                self.vertices[i].y - self.vertices[prev_i].y,
                self.vertices[prev_i].x - self.vertices[i].x,
            )
            e2 = IntVector(
                self.vertices[next_i].y - self.vertices[i].y,
                self.vertices[i].x - self.vertices[next_i].x,
            )

            # Normalize and scale
            len1 = e1.length()
            len2 = e2.length()
            if len1 == 0 or len2 == 0:
                new_vertices.append(self.vertices[i])
                continue

            nx = (e1.x / len1 + e2.x / len2) / 2.0
            ny = (e1.y / len1 + e2.y / len2) / 2.0
            scale = offset / (nx * nx + ny * ny) ** 0.5 if (nx * nx + ny * ny) > 0 else offset

            new_vertices.append(IntPoint(
                round(self.vertices[i].x + nx * scale),
                round(self.vertices[i].y + ny * scale),
            ))
        return PolygonShape(tuple(new_vertices))

    def corner_count(self) -> int:
        return len(self.vertices)

    def corner(self, index: int) -> IntPoint:
        return self.vertices[index % len(self.vertices)]

    def edge_line(self, index: int) -> tuple[IntPoint, IntPoint]:
        n = len(self.vertices)
        return (self.vertices[index % n], self.vertices[(index + 1) % n])

    # -- TileShape interface --

    def split_by_line(self, line_a: int, line_b: int, line_c: int) -> tuple[TileShape | None, TileShape | None]:
        """Split by line ax + by + c = 0."""
        left_verts: list[IntPoint] = []
        right_verts: list[IntPoint] = []
        n = len(self.vertices)

        for i in range(n):
            curr = self.vertices[i]
            next_v = self.vertices[(i + 1) % n]

            curr_val = line_a * curr.x + line_b * curr.y + line_c
            next_val = line_a * next_v.x + line_b * next_v.y + line_c

            if curr_val >= 0:
                left_verts.append(curr)
            if curr_val <= 0:
                right_verts.append(curr)

            # Check for crossing
            if (curr_val > 0 and next_val < 0) or (curr_val < 0 and next_val > 0):
                # Interpolate intersection
                t = curr_val / (curr_val - next_val)
                ix = IntPoint(
                    round(curr.x + t * (next_v.x - curr.x)),
                    round(curr.y + t * (next_v.y - curr.y)),
                )
                left_verts.append(ix)
                right_verts.append(ix)

        left = PolygonShape(tuple(left_verts)) if len(left_verts) >= 3 else None
        right = PolygonShape(tuple(right_verts)) if len(right_verts) >= 3 else None
        return (left, right)

    def center(self) -> FloatPoint:
        cx = sum(v.x for v in self.vertices) / len(self.vertices)
        cy = sum(v.y for v in self.vertices) / len(self.vertices)
        return FloatPoint(cx, cy)

    def max_width(self) -> float:
        bb = self.bounding_box()
        return float(max(bb.width, bb.height))


def _edge_side(edge_start: IntPoint, edge_end: IntPoint, point: IntPoint) -> int:
    """Cross product to determine which side of edge the point is on."""
    return ((edge_end.x - edge_start.x) * (point.y - edge_start.y) -
            (edge_end.y - edge_start.y) * (point.x - edge_start.x))


def _edge_intersection(
    edge_start: IntPoint, edge_end: IntPoint,
    p1: IntPoint, p2: IntPoint,
) -> IntPoint | None:
    """Find intersection of edge and segment p1-p2."""
    x1, y1 = edge_start.x, edge_start.y
    x2, y2 = edge_end.x, edge_end.y
    x3, y3 = p1.x, p1.y
    x4, y4 = p2.x, p2.y

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if denom == 0:
        return None

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    return IntPoint(round(x1 + t * (x2 - x1)), round(y1 + t * (y2 - y1)))
