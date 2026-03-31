"""
IntOctagon - Axis-aligned octagon for 45-degree routing.

Octagons are defined by 4 constraint values (left-x, lower-y, right-x, upper-y)
plus 4 diagonal constraints, matching Freerouting's RegularTileShape concept.
This is the most common tile shape in 45-degree routing mode.
"""

from __future__ import annotations

from dataclasses import dataclass

from kicad_autorouter.geometry.point import FloatPoint, IntPoint
from kicad_autorouter.geometry.shape import BoundingBox, ConvexShape, TileShape


@dataclass(frozen=True, slots=True)
class IntOctagon(TileShape):
    """Axis-aligned octagon defined by 8 half-plane constraints.

    The octagon is the intersection of:
        x >= lx,  x <= rx
        y >= ly,  y <= uy
        x + y >= ulx,  x + y <= lrx   (lower-left / upper-right diagonals)
        x - y >= llx,  x - y <= urx   (upper-left / lower-right diagonals)

    This is equivalent to Freerouting's IntOctagon which uses constraints:
        lx (left x), ly (lower y), rx (right x), uy (upper y),
        ulx (x+y lower bound), lrx (x+y upper bound),
        llx (x-y lower bound), urx (x-y upper bound)
    """

    lx: int   # left x boundary
    ly: int   # lower y boundary (remember: Y increases downward in KiCad)
    rx: int   # right x boundary
    uy: int   # upper y boundary
    ulx: int  # x + y lower bound
    lrx: int  # x + y upper bound
    llx: int  # x - y lower bound
    urx: int  # x - y upper bound

    @staticmethod
    def from_bbox(x_min: int, y_min: int, x_max: int, y_max: int) -> IntOctagon:
        """Create an octagon from axis-aligned bounding box (becomes a rectangle)."""
        return IntOctagon(
            lx=x_min, ly=y_min, rx=x_max, uy=y_max,
            ulx=x_min + y_min, lrx=x_max + y_max,
            llx=x_min - y_max, urx=x_max - y_min,
        )

    @staticmethod
    def from_center_and_radius(cx: int, cy: int, radius: int) -> IntOctagon:
        """Create a regular octagon centered at (cx, cy)."""
        # 45-degree cut depth = radius * (1 - 1/sqrt(2)) ≈ 0.293 * radius
        cut = round(radius * 0.41421356)  # tan(pi/8) * radius for regular octagon
        return IntOctagon(
            lx=cx - radius, ly=cy - radius,
            rx=cx + radius, uy=cy + radius,
            ulx=(cx + cy) - radius - cut,
            lrx=(cx + cy) + radius + cut,
            llx=(cx - cy) - radius - cut,
            urx=(cx - cy) + radius + cut,
        )

    def is_empty(self) -> bool:
        return (self.lx > self.rx or self.ly > self.uy or
                self.ulx > self.lrx or self.llx > self.urx)

    def _vertices(self) -> tuple[IntPoint, ...]:
        """Compute the 4-8 vertices of this octagon (CCW order)."""
        verts: list[IntPoint] = []

        # Bottom-left corner region
        bl_x = max(self.lx, (self.ulx - self.uy + self.lx) // 2 + 1 if False else self.lx)

        # Compute intersection points of the 8 half-planes
        # Going CCW from bottom-left:
        points = []

        # Left-bottom: intersection of x=lx and y=ly, clipped by diagonals
        # We compute all 8 potential corner points
        # Bottom edge (y=ly): from left to right
        x_bl = max(self.lx, self.llx + self.ly)  # x - y >= llx at y=ly
        x_br = min(self.rx, self.ulx - self.ly)   # Wait, we need x + y >= ulx... no

        # Simpler approach: enumerate the 8 edges and find consecutive intersections
        # Edge order (CCW): left, upper-left-diag, top, upper-right-diag,
        #                    right, lower-right-diag, bottom, lower-left-diag

        # For simplicity, compute the vertices by intersecting adjacent constraint lines:
        candidates = [
            # left edge meets bottom edge
            IntPoint(self.lx, self.ly),
            # left edge meets lower-left diagonal (x - y = llx, x = lx) -> y = lx - llx
            IntPoint(self.lx, self.lx - self.llx),
            # lower-left diagonal meets bottom (x - y = llx, y = ly) -> x = llx + ly
            IntPoint(self.llx + self.ly, self.ly),
            # bottom edge meets lower-right diagonal (x + y = ulx, y = ly) -> x = ulx - ly
            IntPoint(self.ulx - self.ly, self.ly),
            # lower-right... this is getting complex. Let's use the 8-point approach.
        ]

        # Practical approach: compute 8 candidate vertices and filter valid ones
        # Vertices are at intersections of adjacent constraint boundaries
        v = [
            # Bottom-left (lx meets bottom or diagonal)
            IntPoint(self.lx, max(self.ly, self.lx - self.llx)),
            # Bottom (if diagonal cuts bottom-left corner)
            IntPoint(max(self.lx, self.llx + self.ly), self.ly),
            # Bottom-right
            IntPoint(min(self.rx, self.ulx - self.ly), self.ly),
            # Right-bottom
            IntPoint(self.rx, max(self.ly, self.ulx - self.rx)),
            # Right-top
            IntPoint(self.rx, min(self.uy, self.rx - self.llx)),
            # Top-right
            IntPoint(min(self.rx, self.urx + self.uy), self.uy),
            # Top-left
            IntPoint(max(self.lx, self.lrx - self.uy), self.uy),
            # Left-top
            IntPoint(self.lx, min(self.uy, self.lrx - self.lx)),
        ]

        # Remove duplicates while preserving order
        result: list[IntPoint] = []
        for p in v:
            if not result or p != result[-1]:
                result.append(p)
        if result and result[0] == result[-1]:
            result.pop()

        return tuple(result) if len(result) >= 3 else tuple(v[:4])

    # -- Shape interface --

    def bounding_box(self) -> BoundingBox:
        return BoundingBox(self.lx, self.ly, self.rx, self.uy)

    def contains(self, point: IntPoint) -> bool:
        x, y = point.x, point.y
        return (self.lx <= x <= self.rx and
                self.ly <= y <= self.uy and
                self.ulx <= x + y <= self.lrx and
                self.llx <= x - y <= self.urx)

    def translate(self, dx: int, dy: int) -> IntOctagon:
        return IntOctagon(
            lx=self.lx + dx, ly=self.ly + dy,
            rx=self.rx + dx, uy=self.uy + dy,
            ulx=self.ulx + dx + dy, lrx=self.lrx + dx + dy,
            llx=self.llx + dx - dy, urx=self.urx + dx - dy,
        )

    def area(self) -> float:
        verts = self._vertices()
        if len(verts) < 3:
            return 0.0
        total = 0
        n = len(verts)
        for i in range(n):
            j = (i + 1) % n
            total += verts[i].x * verts[j].y - verts[j].x * verts[i].y
        return abs(total) / 2.0

    # -- ConvexShape interface --

    def contains_inside(self, point: IntPoint) -> bool:
        x, y = point.x, point.y
        return (self.lx < x < self.rx and
                self.ly < y < self.uy and
                self.ulx < x + y < self.lrx and
                self.llx < x - y < self.urx)

    def intersection(self, other: ConvexShape) -> ConvexShape | None:
        if isinstance(other, IntOctagon):
            result = IntOctagon(
                lx=max(self.lx, other.lx),
                ly=max(self.ly, other.ly),
                rx=min(self.rx, other.rx),
                uy=min(self.uy, other.uy),
                ulx=max(self.ulx, other.ulx),
                lrx=min(self.lrx, other.lrx),
                llx=max(self.llx, other.llx),
                urx=min(self.urx, other.urx),
            )
            return result if not result.is_empty() else None
        return None

    def enlarge(self, offset: int) -> IntOctagon:
        diag_offset = round(offset * 1.41421356)  # offset * sqrt(2)
        return IntOctagon(
            lx=self.lx - offset, ly=self.ly - offset,
            rx=self.rx + offset, uy=self.uy + offset,
            ulx=self.ulx - diag_offset, lrx=self.lrx + diag_offset,
            llx=self.llx - diag_offset, urx=self.urx + diag_offset,
        )

    def corner_count(self) -> int:
        return len(self._vertices())

    def corner(self, index: int) -> IntPoint:
        verts = self._vertices()
        return verts[index % len(verts)]

    def edge_line(self, index: int) -> tuple[IntPoint, IntPoint]:
        verts = self._vertices()
        n = len(verts)
        return (verts[index % n], verts[(index + 1) % n])

    # -- TileShape interface --

    def split_by_line(self, line_a: int, line_b: int, line_c: int) -> tuple[TileShape | None, TileShape | None]:
        """Split by line ax + by + c = 0.

        For axis-aligned and 45-degree lines, we can adjust octagon constraints directly.
        For general lines, we fall back to vertex-based splitting.
        """
        # Check if this is an axis-aligned or 45-degree line
        if line_b == 0 and line_a != 0:
            # Vertical line x = -c/a
            split_x = -line_c // line_a
            if line_a > 0:
                left = IntOctagon(self.lx, self.ly, min(self.rx, split_x), self.uy,
                                  self.ulx, self.lrx, self.llx, self.urx)
                right = IntOctagon(max(self.lx, split_x), self.ly, self.rx, self.uy,
                                   self.ulx, self.lrx, self.llx, self.urx)
            else:
                right = IntOctagon(self.lx, self.ly, min(self.rx, split_x), self.uy,
                                   self.ulx, self.lrx, self.llx, self.urx)
                left = IntOctagon(max(self.lx, split_x), self.ly, self.rx, self.uy,
                                  self.ulx, self.lrx, self.llx, self.urx)
            left_r = left if not left.is_empty() else None
            right_r = right if not right.is_empty() else None
            return (left_r, right_r)

        if line_a == 0 and line_b != 0:
            # Horizontal line y = -c/b
            split_y = -line_c // line_b
            if line_b > 0:
                left = IntOctagon(self.lx, self.ly, self.rx, min(self.uy, split_y),
                                  self.ulx, self.lrx, self.llx, self.urx)
                right = IntOctagon(self.lx, max(self.ly, split_y), self.rx, self.uy,
                                   self.ulx, self.lrx, self.llx, self.urx)
            else:
                right = IntOctagon(self.lx, self.ly, self.rx, min(self.uy, split_y),
                                   self.ulx, self.lrx, self.llx, self.urx)
                left = IntOctagon(self.lx, max(self.ly, split_y), self.rx, self.uy,
                                  self.ulx, self.lrx, self.llx, self.urx)
            left_r = left if not left.is_empty() else None
            right_r = right if not right.is_empty() else None
            return (left_r, right_r)

        # For 45-degree or general lines, use polygon-based splitting
        from kicad_autorouter.geometry.polygon import PolygonShape
        poly = PolygonShape(self._vertices())
        return poly.split_by_line(line_a, line_b, line_c)

    def center(self) -> FloatPoint:
        return FloatPoint(
            (self.lx + self.rx) / 2.0,
            (self.ly + self.uy) / 2.0,
        )

    def max_width(self) -> float:
        return float(max(self.rx - self.lx, self.uy - self.ly))

    # -- Octagon-specific operations --

    def union(self, other: IntOctagon) -> IntOctagon:
        """Bounding octagon containing both."""
        return IntOctagon(
            lx=min(self.lx, other.lx),
            ly=min(self.ly, other.ly),
            rx=max(self.rx, other.rx),
            uy=max(self.uy, other.uy),
            ulx=min(self.ulx, other.ulx),
            lrx=max(self.lrx, other.lrx),
            llx=min(self.llx, other.llx),
            urx=max(self.urx, other.urx),
        )
