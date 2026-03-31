"""
Base class for all physical board items.

Item      - Abstract base for traces, vias, pads, obstacles
FixedState - Whether an item can be moved/removed by the autorouter
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox, Shape

if TYPE_CHECKING:
    from kicad_autorouter.board.board import RoutingBoard


class FixedState(Enum):
    """Whether the autorouter can modify an item."""

    UNFIXED = auto()          # Autorouter can move/remove freely
    SHOVE_FIXED = auto()      # Can be shoved aside but not removed
    SYSTEM_FIXED = auto()     # Cannot be modified (e.g., board outline)
    USER_FIXED = auto()       # User locked this item


@dataclass
class Item(ABC):
    """Abstract base class for all physical board elements.

    Every item has a unique ID, belongs to one or more nets, exists on
    one or more layers, and has spatial extent (bounding box, shape).
    """

    id: int                                     # Unique item identifier
    net_codes: list[int] = field(default_factory=list)  # Nets this item connects
    layer_indices: list[int] = field(default_factory=list)  # Layers this item occupies
    fixed_state: FixedState = FixedState.UNFIXED
    clearance_class: int = 0                    # Index into clearance matrix
    component_id: int = -1                      # Owning component (-1 = none)

    @property
    def net_code(self) -> int:
        """Primary net code (first in list, or 0 if unconnected)."""
        return self.net_codes[0] if self.net_codes else 0

    @property
    def is_fixed(self) -> bool:
        return self.fixed_state != FixedState.UNFIXED

    @property
    def is_connected(self) -> bool:
        return len(self.net_codes) > 0 and self.net_codes[0] > 0

    @abstractmethod
    def bounding_box(self) -> BoundingBox:
        """Axis-aligned bounding box of this item."""
        ...

    @abstractmethod
    def get_shape_on_layer(self, layer_index: int) -> Shape | None:
        """Get the shape of this item on a specific layer."""
        ...

    @abstractmethod
    def translate(self, dx: int, dy: int) -> Item:
        """Return a translated copy."""
        ...

    def is_on_layer(self, layer_index: int) -> bool:
        return layer_index in self.layer_indices

    def shares_net(self, other: Item) -> bool:
        """Check if this item shares any net with another item."""
        return bool(set(self.net_codes) & set(other.net_codes))
