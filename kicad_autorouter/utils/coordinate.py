"""Coordinate transforms between KiCad internal units and routing units.

KiCad uses nanometers as internal units. The router may use a scaled
coordinate system for numerical stability.
"""

from __future__ import annotations

from dataclasses import dataclass

from kicad_autorouter.geometry.point import FloatPoint, IntPoint


@dataclass
class CoordinateTransform:
    """Transforms between board coordinates and routing coordinates.

    KiCad internal units are nanometers (1mm = 1,000,000 nm).
    The router works in the same coordinate space by default.
    """

    scale_factor: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    def board_to_route(self, p: IntPoint) -> IntPoint:
        """Convert board (KiCad) coordinates to routing coordinates."""
        return IntPoint(
            round((p.x - self.offset_x) * self.scale_factor),
            round((p.y - self.offset_y) * self.scale_factor),
        )

    def route_to_board(self, p: IntPoint) -> IntPoint:
        """Convert routing coordinates back to board (KiCad) coordinates."""
        return IntPoint(
            round(p.x / self.scale_factor + self.offset_x),
            round(p.y / self.scale_factor + self.offset_y),
        )

    def board_to_route_float(self, p: FloatPoint) -> FloatPoint:
        return FloatPoint(
            (p.x - self.offset_x) * self.scale_factor,
            (p.y - self.offset_y) * self.scale_factor,
        )

    def route_to_board_float(self, p: FloatPoint) -> FloatPoint:
        return FloatPoint(
            p.x / self.scale_factor + self.offset_x,
            p.y / self.scale_factor + self.offset_y,
        )

    def board_to_route_distance(self, distance: int) -> int:
        """Convert a distance/width from board to routing coordinates."""
        return round(distance * self.scale_factor)

    def route_to_board_distance(self, distance: int) -> int:
        return round(distance / self.scale_factor)

    @staticmethod
    def identity() -> CoordinateTransform:
        return CoordinateTransform(1.0, 0.0, 0.0)
