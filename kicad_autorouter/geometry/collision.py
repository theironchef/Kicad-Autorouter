"""
Collision detection for PCB routing.

Provides segment-level collision tests that go beyond bounding-box checks:
- Segment vs expanded octagon (trace clearance checking)
- Segment vs segment (trace-to-trace crossing)
- Point vs expanded shape (via clearance checking)

All coordinates in nanometers. All functions are pure (no side effects).
"""

from __future__ import annotations

import math

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.geometry.octagon import IntOctagon


def segments_intersect(
    a1: IntPoint, a2: IntPoint,
    b1: IntPoint, b2: IntPoint,
) -> bool:
    """Test whether two line segments (a1-a2) and (b1-b2) intersect.

    Uses the cross-product orientation method. Returns True if the segments
    share any point (including endpoints).
    """
    d1 = _cross(b1, b2, a1)
    d2 = _cross(b1, b2, a2)
    d3 = _cross(a1, a2, b1)
    d4 = _cross(a1, a2, b2)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True

    # Collinear cases
    if d1 == 0 and _on_segment(b1, b2, a1):
        return True
    if d2 == 0 and _on_segment(b1, b2, a2):
        return True
    if d3 == 0 and _on_segment(a1, a2, b1):
        return True
    if d4 == 0 and _on_segment(a1, a2, b2):
        return True

    return False


def segment_intersects_octagon(
    p1: IntPoint, p2: IntPoint,
    octagon: IntOctagon,
) -> bool:
    """Test whether a line segment intersects an IntOctagon.

    Returns True if any part of the segment is inside or crosses the octagon.
    Uses the 8 half-plane constraints of the octagon for efficient testing.
    """
    # Quick reject: bounding box
    seg_bb = BoundingBox(
        min(p1.x, p2.x), min(p1.y, p2.y),
        max(p1.x, p2.x), max(p1.y, p2.y),
    )
    oct_bb = octagon.bounding_box()
    if not seg_bb.intersects(oct_bb):
        return False

    # If either endpoint is inside the octagon, they intersect
    if octagon.contains(p1) or octagon.contains(p2):
        return True

    # Test segment against each of the 8 edges of the octagon
    verts = octagon._vertices()
    n = len(verts)
    if n < 3:
        return False

    for i in range(n):
        j = (i + 1) % n
        if segments_intersect(p1, p2, verts[i], verts[j]):
            return True

    return False


def segment_clearance_to_segment(
    a1: IntPoint, a2: IntPoint,
    b1: IntPoint, b2: IntPoint,
) -> float:
    """Minimum distance between two line segments.

    Used for clearance checking between traces.
    """
    if segments_intersect(a1, a2, b1, b2):
        return 0.0

    # Minimum of all point-to-segment distances
    return min(
        _point_to_segment_dist(a1, b1, b2),
        _point_to_segment_dist(a2, b1, b2),
        _point_to_segment_dist(b1, a1, a2),
        _point_to_segment_dist(b2, a1, a2),
    )


def segment_clearance_to_octagon(
    p1: IntPoint, p2: IntPoint,
    octagon: IntOctagon,
) -> float:
    """Minimum distance from a line segment to an octagon boundary.

    Returns 0 if the segment intersects the octagon.
    """
    if segment_intersects_octagon(p1, p2, octagon):
        return 0.0

    verts = octagon._vertices()
    n = len(verts)
    if n < 3:
        return float('inf')

    min_dist = float('inf')

    # Check distance from segment endpoints to octagon edges
    for i in range(n):
        j = (i + 1) % n
        d = segment_clearance_to_segment(p1, p2, verts[i], verts[j])
        if d < min_dist:
            min_dist = d

    return min_dist


def expanded_segment_intersects_items(
    p1: IntPoint, p2: IntPoint,
    half_width: int,
    clearance: int,
    layer_index: int,
    net_code: int,
    items: list,
) -> bool:
    """Check if a trace segment (with width and clearance) conflicts with items.

    This is the main collision check used by the engine and optimizer.
    Expands the trace segment into an octagon and checks against item shapes.

    Args:
        p1, p2: Segment endpoints.
        half_width: Half the trace width.
        clearance: Required clearance to other items.
        layer_index: Copper layer to check.
        net_code: Net code of the trace (same-net items are skipped).
        items: List of board Items to check against.

    Returns:
        True if any conflict is found.
    """
    total_expand = half_width + clearance

    # Build expanded bounding box for the segment
    seg_bb = BoundingBox(
        min(p1.x, p2.x) - total_expand,
        min(p1.y, p2.y) - total_expand,
        max(p1.x, p2.x) + total_expand,
        max(p1.y, p2.y) + total_expand,
    )

    for item in items:
        # Skip same-net items
        if net_code in item.net_codes:
            continue

        # Skip items not on this layer
        if not item.is_on_layer(layer_index):
            continue

        # Quick bbox reject
        item_bb = item.bounding_box()
        if not item_bb.intersects(seg_bb):
            continue

        # Get item's shape on this layer
        item_shape = item.get_shape_on_layer(layer_index)
        if item_shape is None:
            continue

        # Expand item shape by our clearance requirement
        if isinstance(item_shape, IntOctagon):
            expanded = item_shape.enlarge(total_expand)
            if segment_intersects_octagon(p1, p2, expanded):
                return True
        else:
            # Fallback: expanded bounding box check
            expanded_bb = item_bb.enlarge(total_expand)
            if expanded_bb.contains(p1) or expanded_bb.contains(p2):
                return True

    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cross(o: IntPoint, a: IntPoint, b: IntPoint) -> int:
    """Cross product of vectors (a - o) and (b - o)."""
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)


def _on_segment(p: IntPoint, q: IntPoint, r: IntPoint) -> bool:
    """Check if point r lies on segment p-q (assuming collinear)."""
    return (min(p.x, q.x) <= r.x <= max(p.x, q.x) and
            min(p.y, q.y) <= r.y <= max(p.y, q.y))


def _point_to_segment_dist(p: IntPoint, a: IntPoint, b: IntPoint) -> float:
    """Shortest distance from point p to line segment a-b."""
    dx = b.x - a.x
    dy = b.y - a.y
    len_sq = dx * dx + dy * dy

    if len_sq == 0:
        # Degenerate segment (a == b)
        return math.sqrt((p.x - a.x) ** 2 + (p.y - a.y) ** 2)

    # Project p onto the line, clamped to [0, 1]
    t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len_sq))

    proj_x = a.x + t * dx
    proj_y = a.y + t * dy

    return math.sqrt((p.x - proj_x) ** 2 + (p.y - proj_y) ** 2)
