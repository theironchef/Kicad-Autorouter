"""
Component representation.

A Component is a placed footprint on the board, containing pads.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox


@dataclass
class Component:
    """A placed component (footprint) on the PCB."""

    id: int
    reference: str          # e.g., "U1", "R3", "C5"
    value: str = ""         # e.g., "STM32F103", "10k", "100nF"
    footprint: str = ""     # Footprint library name
    position: IntPoint = IntPoint(0, 0)
    rotation_deg: float = 0.0
    is_on_front: bool = True  # True = front side, False = back side
    is_locked: bool = False

    # Pad IDs belonging to this component
    pad_ids: list[int] = field(default_factory=list)

    @property
    def side(self) -> str:
        return "front" if self.is_on_front else "back"

    def bounding_box(self) -> BoundingBox | None:
        """Approximate bounding box (requires pad info for accuracy)."""
        # This is a placeholder; actual bbox computed from pad extents
        return BoundingBox(
            self.position.x - 1_000_000,
            self.position.y - 1_000_000,
            self.position.x + 1_000_000,
            self.position.y + 1_000_000,
        )
