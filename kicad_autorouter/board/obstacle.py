"""
Obstacle area representation.

ObstacleAreas are keepout zones or filled zones that the autorouter
must route around. They can be on specific layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.polygon import Polygon
from kicad_autorouter.geometry.shape import BoundingBox, Shape
from kicad_autorouter.geometry.octagon import IntOctagon
from kicad_autorouter.board.item import Item


class ObstacleType(Enum):
    """Type of obstacle area."""
    KEEPOUT = auto()       # No routing allowed
    VIA_KEEPOUT = auto()   # No vias allowed
    COPPER_FILL = auto()   # Filled copper zone
    BOARD_OUTLINE = auto() # Board boundary


@dataclass
class ObstacleArea(Item):
    """A region that constrains routing.

    Can represent keepout zones, filled zones, or the board outline.
    """

    vertices: list[IntPoint] = field(default_factory=list)
    obstacle_type: ObstacleType = ObstacleType.KEEPOUT

    @property
    def polygon(self) -> Polygon | None:
        if len(self.vertices) >= 3:
            return Polygon(tuple(self.vertices))
        return None

    def bounding_box(self) -> BoundingBox:
        if not self.vertices:
            return BoundingBox(0, 0, 0, 0)
        return BoundingBox.from_points(self.vertices)

    def get_shape_on_layer(self, layer_index: int) -> Shape | None:
        if layer_index not in self.layer_indices:
            return None
        bb = self.bounding_box()
        return IntOctagon.from_bbox(bb.x_min, bb.y_min, bb.x_max, bb.y_max)

    def contains_point(self, point: IntPoint) -> bool:
        poly = self.polygon
        return poly.contains(point) if poly else False

    def translate(self, dx: int, dy: int) -> ObstacleArea:
        return ObstacleArea(
            id=self.id,
            net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:],
            fixed_state=self.fixed_state,
            clearance_class=self.clearance_class,
            component_id=self.component_id,
            vertices=[p.translate_by(dx, dy) for p in self.vertices],
            obstacle_type=self.obstacle_type,
        )
