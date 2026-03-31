"""
PCB layer definitions.

Layer       - Single copper or non-copper layer
LayerType   - Enumeration of layer types (signal, power, mixed)
LayerStructure - Ordered collection of all board layers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class LayerType(Enum):
    """Type of a PCB layer."""
    SIGNAL = auto()    # Signal routing layer
    POWER = auto()     # Power plane
    MIXED = auto()     # Both signal and power
    JUMPER = auto()    # Jumper/bridge layer


@dataclass
class Layer:
    """A single PCB layer."""

    index: int           # 0-based layer index
    name: str            # Layer name (e.g., "F.Cu", "B.Cu", "In1.Cu")
    layer_type: LayerType = LayerType.SIGNAL
    is_active: bool = True

    # Routing constraints for this layer
    preferred_direction: str = "any"  # "horizontal", "vertical", "any", "45"

    def is_signal(self) -> bool:
        return self.layer_type in (LayerType.SIGNAL, LayerType.MIXED)


@dataclass
class LayerStructure:
    """Ordered collection of all PCB layers.

    Layers are ordered from top (front copper) to bottom (back copper).
    """

    layers: list[Layer] = field(default_factory=list)

    @property
    def copper_layer_count(self) -> int:
        return len(self.layers)

    def get_layer(self, index: int) -> Layer:
        return self.layers[index]

    def get_layer_by_name(self, name: str) -> Layer | None:
        for layer in self.layers:
            if layer.name == name:
                return layer
        return None

    def get_layer_index(self, name: str) -> int:
        for layer in self.layers:
            if layer.name == name:
                return layer.index
        return -1

    @property
    def top_layer(self) -> int:
        """Index of the top (front) copper layer."""
        return 0

    @property
    def bottom_layer(self) -> int:
        """Index of the bottom (back) copper layer."""
        return len(self.layers) - 1

    def is_adjacent(self, layer1: int, layer2: int) -> bool:
        """Check if two layers are adjacent in the stackup."""
        return abs(layer1 - layer2) == 1

    def layers_between(self, layer1: int, layer2: int) -> list[int]:
        """Return indices of layers between (exclusive) layer1 and layer2."""
        lo, hi = min(layer1, layer2), max(layer1, layer2)
        return list(range(lo + 1, hi))

    @staticmethod
    def create_default(copper_count: int = 2) -> LayerStructure:
        """Create a standard layer structure with given copper layer count."""
        layers = []
        if copper_count >= 1:
            layers.append(Layer(0, "F.Cu", LayerType.SIGNAL))
        for i in range(1, copper_count - 1):
            layers.append(Layer(i, f"In{i}.Cu", LayerType.SIGNAL))
        if copper_count >= 2:
            layers.append(Layer(copper_count - 1, "B.Cu", LayerType.SIGNAL))
        return LayerStructure(layers)
