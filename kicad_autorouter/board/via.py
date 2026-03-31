"""
Via representation.

A Via is a vertical connection between two copper layers, consisting of
a drilled hole with a copper pad on each connected layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from kicad_autorouter.geometry.octagon import IntOctagon
from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox, Shape
from kicad_autorouter.board.item import Item


@dataclass
class Via(Item):
    """A via connecting two or more copper layers.

    Defined by position, diameter, drill size, and layer span.
    """

    position: IntPoint = IntPoint(0, 0)
    diameter: int = 800_000      # Via pad diameter (nanometers)
    drill: int = 400_000         # Drill diameter (nanometers)
    start_layer: int = 0         # First layer (usually F.Cu = 0)
    end_layer: int = 1           # Last layer (usually B.Cu = 1)

    def __post_init__(self):
        # Ensure layer_indices covers the full span
        if not self.layer_indices:
            self.layer_indices = list(range(self.start_layer, self.end_layer + 1))

    @property
    def radius(self) -> int:
        return self.diameter // 2

    @property
    def drill_radius(self) -> int:
        return self.drill // 2

    # Note: total_layers is set by RoutingBoard when via is added
    _total_layers: int = 2

    @property
    def is_through(self) -> bool:
        """True if via spans from first to last layer."""
        return self.start_layer == 0 and self.end_layer == self._total_layers - 1

    @property
    def is_blind(self) -> bool:
        """True if via connects an outer layer to an inner layer."""
        touches_outer = self.start_layer == 0 or self.end_layer == self._total_layers - 1
        return touches_outer and not self.is_through

    @property
    def is_buried(self) -> bool:
        """True if via connects only inner layers."""
        return self.start_layer > 0 and self.end_layer < self._total_layers - 1

    def bounding_box(self) -> BoundingBox:
        r = self.radius
        return BoundingBox(
            self.position.x - r, self.position.y - r,
            self.position.x + r, self.position.y + r,
        )

    def get_shape_on_layer(self, layer_index: int) -> Shape | None:
        if layer_index not in self.layer_indices:
            return None
        return IntOctagon.from_center_and_radius(
            self.position.x, self.position.y, self.radius
        )

    def get_clearance_shape(self, layer_index: int, clearance: int) -> IntOctagon | None:
        if layer_index not in self.layer_indices:
            return None
        return IntOctagon.from_center_and_radius(
            self.position.x, self.position.y, self.radius + clearance
        )

    def translate(self, dx: int, dy: int) -> Via:
        return Via(
            id=self.id,
            net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:],
            fixed_state=self.fixed_state,
            clearance_class=self.clearance_class,
            component_id=self.component_id,
            position=self.position.translate_by(dx, dy),
            diameter=self.diameter,
            drill=self.drill,
            start_layer=self.start_layer,
            end_layer=self.end_layer,
        )

    def connects_layer(self, layer_index: int) -> bool:
        return self.start_layer <= layer_index <= self.end_layer

    def center(self) -> IntPoint:
        return self.position
