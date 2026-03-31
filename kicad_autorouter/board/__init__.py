"""
PCB board data model.

Represents all physical elements on a PCB: traces, vias, pads, components,
nets, and layers. This is the central data structure that the autorouter
reads from and writes to.
"""

from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.item import Item, FixedState
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.via import Via
from kicad_autorouter.board.component import Component
from kicad_autorouter.board.obstacle import ObstacleArea
from kicad_autorouter.board.board import RoutingBoard

__all__ = [
    "Layer", "LayerStructure", "LayerType",
    "Net", "NetClass",
    "Item", "FixedState",
    "Pad", "PadShape",
    "Trace",
    "Via",
    "Component",
    "ObstacleArea",
    "RoutingBoard",
]
