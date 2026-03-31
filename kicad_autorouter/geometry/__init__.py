"""
Geometry primitives for PCB autorouting.

Provides integer-coordinate 2D geometry optimized for PCB operations:
points, vectors, directions, lines, and shapes (convex polygons, octagons).
All coordinates use nanometer-scale integers matching KiCad's internal units.
"""

from kicad_autorouter.geometry.point import FloatPoint, IntPoint
from kicad_autorouter.geometry.vector import IntVector, FloatVector
from kicad_autorouter.geometry.direction import Direction, Direction45
from kicad_autorouter.geometry.line import Line, LineSegment
from kicad_autorouter.geometry.shape import Shape, ConvexShape, TileShape
from kicad_autorouter.geometry.polygon import Polygon, Polyline, PolygonShape
from kicad_autorouter.geometry.octagon import IntOctagon
from kicad_autorouter.geometry.side import Side
from kicad_autorouter.geometry.collision import (
    segments_intersect,
    segment_intersects_octagon,
    segment_clearance_to_segment,
    segment_clearance_to_octagon,
    expanded_segment_intersects_items,
)

__all__ = [
    "FloatPoint", "IntPoint",
    "IntVector", "FloatVector",
    "Direction", "Direction45",
    "Line", "LineSegment",
    "Shape", "ConvexShape", "TileShape",
    "Polygon", "Polyline", "PolygonShape",
    "IntOctagon",
    "Side",
]
