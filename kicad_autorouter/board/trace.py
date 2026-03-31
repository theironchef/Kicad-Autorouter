"""
Trace (track) representation.

A Trace is a routed copper path on a single layer, defined by a polyline
of corner points and a uniform width.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.polygon import Polyline
from kicad_autorouter.geometry.shape import BoundingBox, Shape
from kicad_autorouter.geometry.octagon import IntOctagon
from kicad_autorouter.board.item import Item


@dataclass
class Trace(Item):
    """A routed copper trace on a single layer.

    Defined by an ordered sequence of corner points (polyline) and
    a uniform width. All segments share the same width and layer.
    """

    corners: list[IntPoint] = field(default_factory=list)
    width: int = 0            # Track width in nanometers
    layer_index: int = 0      # Which copper layer this trace is on

    def __post_init__(self):
        if self.layer_index not in self.layer_indices:
            self.layer_indices = [self.layer_index]

    @property
    def half_width(self) -> int:
        return self.width // 2

    @property
    def segment_count(self) -> int:
        return max(0, len(self.corners) - 1)

    @property
    def polyline(self) -> Polyline | None:
        if len(self.corners) >= 2:
            return Polyline(tuple(self.corners))
        return None

    @property
    def first_corner(self) -> IntPoint | None:
        return self.corners[0] if self.corners else None

    @property
    def last_corner(self) -> IntPoint | None:
        return self.corners[-1] if self.corners else None

    def total_length(self) -> float:
        pl = self.polyline
        return pl.total_length() if pl else 0.0

    def bounding_box(self) -> BoundingBox:
        if not self.corners:
            return BoundingBox(0, 0, 0, 0)
        hw = self.half_width
        xs = [p.x for p in self.corners]
        ys = [p.y for p in self.corners]
        return BoundingBox(
            min(xs) - hw, min(ys) - hw,
            max(xs) + hw, max(ys) + hw,
        )

    def get_shape_on_layer(self, layer_index: int) -> Shape | None:
        if layer_index != self.layer_index:
            return None
        # Return bounding octagon as approximation
        bb = self.bounding_box()
        return IntOctagon.from_bbox(bb.x_min, bb.y_min, bb.x_max, bb.y_max)

    def get_segment_shape(self, seg_index: int) -> IntOctagon:
        """Get the octagonal shape of a single trace segment (with width)."""
        p1 = self.corners[seg_index]
        p2 = self.corners[seg_index + 1]
        hw = self.half_width
        return IntOctagon.from_bbox(
            min(p1.x, p2.x) - hw, min(p1.y, p2.y) - hw,
            max(p1.x, p2.x) + hw, max(p1.y, p2.y) + hw,
        )

    def translate(self, dx: int, dy: int) -> Trace:
        return Trace(
            id=self.id,
            net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:],
            fixed_state=self.fixed_state,
            clearance_class=self.clearance_class,
            component_id=self.component_id,
            corners=[p.translate_by(dx, dy) for p in self.corners],
            width=self.width,
            layer_index=self.layer_index,
        )

    def split_at(self, corner_index: int) -> tuple[Trace, Trace]:
        """Split trace at a corner point into two traces."""
        first_corners = self.corners[:corner_index + 1]
        second_corners = self.corners[corner_index:]
        t1 = Trace(
            id=self.id, net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:], fixed_state=self.fixed_state,
            clearance_class=self.clearance_class,
            corners=first_corners, width=self.width,
            layer_index=self.layer_index,
        )
        t2 = Trace(
            id=0, net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:], fixed_state=self.fixed_state,
            clearance_class=self.clearance_class,
            corners=second_corners, width=self.width,
            layer_index=self.layer_index,
        )
        return (t1, t2)

    def reverse(self) -> Trace:
        """Return a copy with reversed corner order."""
        return Trace(
            id=self.id, net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:], fixed_state=self.fixed_state,
            clearance_class=self.clearance_class, component_id=self.component_id,
            corners=list(reversed(self.corners)), width=self.width,
            layer_index=self.layer_index,
        )

    # ----- Point-based splitting -----

    def split_at_point(self, point: IntPoint) -> tuple[Trace, Trace] | None:
        """Split trace at an arbitrary point on (or near) its path.

        Finds the closest segment, projects the point onto it, inserts the
        projected point as a new corner, and splits there. Returns None if
        the point is not within half_width distance of any segment.
        """
        best_seg = -1
        best_dist_sq = float('inf')
        best_proj = point

        for i in range(self.segment_count):
            p1 = self.corners[i]
            p2 = self.corners[i + 1]
            proj, dist_sq = _project_onto_segment(point, p1, p2)
            if dist_sq < best_dist_sq:
                best_dist_sq = dist_sq
                best_seg = i
                best_proj = proj

        if best_seg < 0 or best_dist_sq > (self.half_width + 1000) ** 2:
            return None

        # Build two corner lists split at the projection point
        first = self.corners[:best_seg + 1] + [best_proj]
        second = [best_proj] + self.corners[best_seg + 1:]

        if len(first) < 2 or len(second) < 2:
            return None

        t1 = Trace(
            id=self.id, net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:], fixed_state=self.fixed_state,
            clearance_class=self.clearance_class,
            corners=first, width=self.width, layer_index=self.layer_index,
        )
        t2 = Trace(
            id=0, net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:], fixed_state=self.fixed_state,
            clearance_class=self.clearance_class,
            corners=second, width=self.width, layer_index=self.layer_index,
        )
        return (t1, t2)

    # ----- Combination -----

    def can_combine_with(self, other: Trace) -> bool:
        """Check if two traces can be merged into one.

        Traces can combine if they share the same net, layer, width, and
        have a matching endpoint (first/last corners within tolerance).
        """
        if self.layer_index != other.layer_index:
            return False
        if self.width != other.width:
            return False
        if not self.shares_net(other):
            return False
        if self.first_corner is None or other.first_corner is None:
            return False

        tol_sq = (self.half_width + 100) ** 2
        # Check all four endpoint combos
        for my_end in (self.first_corner, self.last_corner):
            for their_end in (other.first_corner, other.last_corner):
                if my_end is not None and their_end is not None:
                    if my_end.distance_squared(their_end) <= tol_sq:
                        return True
        return False

    def combine_with(self, other: Trace) -> Trace | None:
        """Merge another trace onto this one at a shared endpoint.

        Returns a new Trace with combined corners, or None if they
        don't share an endpoint.
        """
        if not self.can_combine_with(other):
            return None

        tol_sq = (self.half_width + 100) ** 2

        # Find matching endpoint pair
        my_last = self.last_corner
        my_first = self.first_corner
        ot_first = other.first_corner
        ot_last = other.last_corner

        if my_last and ot_first and my_last.distance_squared(ot_first) <= tol_sq:
            new_corners = self.corners + other.corners[1:]
        elif my_last and ot_last and my_last.distance_squared(ot_last) <= tol_sq:
            new_corners = self.corners + list(reversed(other.corners))[1:]
        elif my_first and ot_last and my_first.distance_squared(ot_last) <= tol_sq:
            new_corners = other.corners + self.corners[1:]
        elif my_first and ot_first and my_first.distance_squared(ot_first) <= tol_sq:
            new_corners = list(reversed(other.corners)) + self.corners[1:]
        else:
            return None

        return Trace(
            id=self.id, net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:], fixed_state=self.fixed_state,
            clearance_class=self.clearance_class,
            corners=new_corners, width=self.width, layer_index=self.layer_index,
        )

    # ----- Overlap and cycle detection -----

    def has_overlap_with(self, other: Trace) -> bool:
        """Check if two traces have overlapping segments on the same layer."""
        if self.layer_index != other.layer_index:
            return False
        if not self.shares_net(other):
            return False

        # Check if any segment from self overlaps any segment from other
        for i in range(self.segment_count):
            s1 = self.corners[i]
            s2 = self.corners[i + 1]
            seg_bb = BoundingBox(
                min(s1.x, s2.x) - self.half_width,
                min(s1.y, s2.y) - self.half_width,
                max(s1.x, s2.x) + self.half_width,
                max(s1.y, s2.y) + self.half_width,
            )
            for j in range(other.segment_count):
                o1 = other.corners[j]
                o2 = other.corners[j + 1]
                other_bb = BoundingBox(
                    min(o1.x, o2.x) - other.half_width,
                    min(o1.y, o2.y) - other.half_width,
                    max(o1.x, o2.x) + other.half_width,
                    max(o1.y, o2.y) + other.half_width,
                )
                if seg_bb.intersects(other_bb):
                    # Check if segments are nearly collinear and overlapping
                    if _segments_overlap(s1, s2, o1, o2, self.half_width):
                        return True
        return False

    # ----- Tail detection -----

    def get_uncontacted_endpoints(
        self,
        contact_points: list[IntPoint],
        tolerance: int = 0,
    ) -> list[IntPoint]:
        """Return endpoints that don't touch any contact point.

        A "tail" is a trace endpoint that doesn't connect to a pad, via,
        or another trace. Returns 0, 1, or 2 endpoints.
        """
        tails = []
        if tolerance <= 0:
            tolerance = self.half_width + 1000

        tol_sq = tolerance ** 2

        for ep in (self.first_corner, self.last_corner):
            if ep is None:
                continue
            contacted = any(ep.distance_squared(cp) <= tol_sq for cp in contact_points)
            if not contacted:
                tails.append(ep)
        return tails

    # ----- Shove support -----

    def translate_segment(self, seg_index: int, dx: int, dy: int) -> Trace:
        """Return a new trace with one segment translated (shoved).

        The two endpoints of the specified segment are moved by (dx, dy).
        Adjacent segments are adjusted to connect properly.
        """
        if seg_index < 0 or seg_index >= self.segment_count:
            return self

        new_corners = list(self.corners)
        new_corners[seg_index] = new_corners[seg_index].translate_by(dx, dy)
        new_corners[seg_index + 1] = new_corners[seg_index + 1].translate_by(dx, dy)

        return Trace(
            id=self.id, net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:], fixed_state=self.fixed_state,
            clearance_class=self.clearance_class, component_id=self.component_id,
            corners=new_corners, width=self.width, layer_index=self.layer_index,
        )


# ----- Module-level helpers -----

def _project_onto_segment(
    point: IntPoint, seg_a: IntPoint, seg_b: IntPoint,
) -> tuple[IntPoint, float]:
    """Project a point onto a line segment. Returns (projected_point, dist_squared)."""
    dx = seg_b.x - seg_a.x
    dy = seg_b.y - seg_a.y
    len_sq = dx * dx + dy * dy
    if len_sq == 0:
        d = point.distance_squared(seg_a)
        return seg_a, d

    t = max(0.0, min(1.0,
        ((point.x - seg_a.x) * dx + (point.y - seg_a.y) * dy) / len_sq
    ))
    proj = IntPoint(round(seg_a.x + t * dx), round(seg_a.y + t * dy))
    return proj, point.distance_squared(proj)


def _segments_overlap(
    a1: IntPoint, a2: IntPoint, b1: IntPoint, b2: IntPoint,
    tolerance: int,
) -> bool:
    """Check if two segments are nearly collinear and overlap spatially."""
    # Check collinearity via cross product
    dx_a = a2.x - a1.x
    dy_a = a2.y - a1.y
    len_a = math.sqrt(dx_a * dx_a + dy_a * dy_a)
    if len_a == 0:
        return False

    # Distance from b1 and b2 to the line through a1-a2
    cross1 = abs((b1.x - a1.x) * dy_a - (b1.y - a1.y) * dx_a) / len_a
    cross2 = abs((b2.x - a1.x) * dy_a - (b2.y - a1.y) * dx_a) / len_a

    if cross1 > tolerance or cross2 > tolerance:
        return False  # Not collinear enough

    # Check overlap along the line direction
    proj_b1 = ((b1.x - a1.x) * dx_a + (b1.y - a1.y) * dy_a) / len_a
    proj_b2 = ((b2.x - a1.x) * dx_a + (b2.y - a1.y) * dy_a) / len_a
    proj_min = min(proj_b1, proj_b2)
    proj_max = max(proj_b1, proj_b2)

    return proj_max > 0 and proj_min < len_a
