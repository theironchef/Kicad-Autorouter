"""
Pad (pin) representation.

Pads are the connection points on component footprints. They are the
source/destination endpoints for autorouting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from kicad_autorouter.geometry.octagon import IntOctagon
from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox, Shape
from kicad_autorouter.board.item import Item


class PadShape(Enum):
    """Shape of a pad's copper area."""

    CIRCLE = auto()
    RECTANGLE = auto()
    OVAL = auto()
    ROUNDRECT = auto()
    TRAPEZOID = auto()
    CUSTOM = auto()


@dataclass
class Pad(Item):
    """A component pad / pin.

    Pads are the endpoints that the autorouter must connect. Each pad
    has a position, size, shape, and belongs to a net.
    """

    position: IntPoint = IntPoint(0, 0)
    size_x: int = 0          # Pad width in nanometers
    size_y: int = 0          # Pad height in nanometers
    drill_diameter: int = 0  # Through-hole drill size (0 for SMD)
    pad_shape: PadShape = PadShape.CIRCLE
    rotation_deg: float = 0.0

    # Component context
    pad_name: str = ""       # Pad name/number within component (e.g., "1", "A1")
    component_ref: str = ""  # Owning component reference (e.g., "U1")

    @property
    def is_through_hole(self) -> bool:
        return self.drill_diameter > 0

    @property
    def is_smd(self) -> bool:
        return self.drill_diameter == 0

    def bounding_box(self) -> BoundingBox:
        half_x = self.size_x // 2
        half_y = self.size_y // 2
        return BoundingBox(
            self.position.x - half_x,
            self.position.y - half_y,
            self.position.x + half_x,
            self.position.y + half_y,
        )

    def get_shape_on_layer(self, layer_index: int) -> Shape | None:
        if layer_index not in self.layer_indices:
            return None
        # Return octagon approximation of pad shape
        half_x = self.size_x // 2
        half_y = self.size_y // 2
        if self.pad_shape == PadShape.CIRCLE:
            radius = max(half_x, half_y)
            return IntOctagon.from_center_and_radius(
                self.position.x, self.position.y, radius
            )
        else:
            # Rectangle / oval approximation
            return IntOctagon.from_bbox(
                self.position.x - half_x,
                self.position.y - half_y,
                self.position.x + half_x,
                self.position.y + half_y,
            )

    def translate(self, dx: int, dy: int) -> Pad:
        return Pad(
            id=self.id,
            net_codes=self.net_codes[:],
            layer_indices=self.layer_indices[:],
            fixed_state=self.fixed_state,
            clearance_class=self.clearance_class,
            component_id=self.component_id,
            position=self.position.translate_by(dx, dy),
            size_x=self.size_x,
            size_y=self.size_y,
            drill_diameter=self.drill_diameter,
            pad_shape=self.pad_shape,
            rotation_deg=self.rotation_deg,
            pad_name=self.pad_name,
            component_ref=self.component_ref,
        )

    def center(self) -> IntPoint:
        return self.position

    def get_clearance_shape(self, clearance: int) -> IntOctagon:
        """Pad shape enlarged by clearance distance."""
        half_x = self.size_x // 2 + clearance
        half_y = self.size_y // 2 + clearance
        if self.pad_shape == PadShape.CIRCLE:
            radius = max(half_x, half_y)
            return IntOctagon.from_center_and_radius(
                self.position.x, self.position.y, radius
            )
        return IntOctagon.from_bbox(
            self.position.x - half_x,
            self.position.y - half_y,
            self.position.x + half_x,
            self.position.y + half_y,
        )
