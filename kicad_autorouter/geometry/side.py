"""
Side enumeration for geometric orientation tests.
"""

from enum import IntEnum


class Side(IntEnum):
    """Result of a point-relative-to-line orientation test."""

    ON_THE_LEFT = 1
    ON_THE_RIGHT = -1
    COLLINEAR = 0

    @staticmethod
    def of(cross_product: int | float) -> "Side":
        if cross_product > 0:
            return Side.ON_THE_LEFT
        elif cross_product < 0:
            return Side.ON_THE_RIGHT
        return Side.COLLINEAR

    def negate(self) -> "Side":
        return Side(-self.value) if self != Side.COLLINEAR else Side.COLLINEAR
