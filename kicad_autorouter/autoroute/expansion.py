"""
Expansion room system for the maze search.

The autorouter divides free board space into convex tile-shaped regions
(expansion rooms). Rooms connect via doors (shared boundaries) and drills
(layer transitions). The maze search explores room-to-room rather than
pixel-by-pixel, making it efficient for large boards.

This is the core spatial abstraction from Freerouting's autorouter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from kicad_autorouter.geometry.point import FloatPoint, IntPoint
from kicad_autorouter.geometry.shape import BoundingBox, TileShape
from kicad_autorouter.geometry.octagon import IntOctagon

if TYPE_CHECKING:
    from kicad_autorouter.board.item import Item

logger = logging.getLogger(__name__)


class RoomType(Enum):
    """Type of expansion room."""
    FREE_SPACE = auto()         # Unobstructed routing area
    OBSTACLE = auto()           # Blocked by an item (pad, trace, obstacle)
    TARGET = auto()             # Destination pad/target area


@dataclass
class ExpansionRoom:
    """A convex spatial region in the expansion room graph.

    Each room exists on a single layer and has a tile shape defining its
    boundary. Rooms connect to neighbors via ExpansionDoors (shared
    boundary segments) and to other layers via ExpansionDrills.
    """

    id: int
    shape: TileShape            # Convex tile defining room boundary
    layer_index: int            # Which copper layer
    room_type: RoomType = RoomType.FREE_SPACE

    # Item that blocks this room (for OBSTACLE rooms)
    blocking_item: Item | None = None

    # Connectivity
    doors: list[ExpansionDoor] = field(default_factory=list)
    drills: list[ExpansionDrill] = field(default_factory=list)

    # Maze search state (reset each search)
    reached: bool = False
    reached_from_door: ExpansionDoor | None = None
    reached_from_drill: ExpansionDrill | None = None
    cost_to_here: float = float('inf')

    @property
    def center(self) -> FloatPoint:
        return self.shape.center()

    @property
    def is_free(self) -> bool:
        return self.room_type == RoomType.FREE_SPACE

    @property
    def is_target(self) -> bool:
        return self.room_type == RoomType.TARGET

    def reset_search_state(self):
        """Clear maze search markers for a new search."""
        self.reached = False
        self.reached_from_door = None
        self.reached_from_drill = None
        self.cost_to_here = float('inf')

    def contains_point(self, point: IntPoint) -> bool:
        return self.shape.contains(point)

    def overlaps_bbox(self, bbox: BoundingBox) -> bool:
        return self.shape.bounding_box().intersects(bbox)


@dataclass
class ExpansionDoor:
    """A connection between two adjacent expansion rooms on the same layer.

    Represents a shared boundary segment through which a trace can pass.
    The door has a position (midpoint of shared boundary) and a width
    (length of shared boundary, constraining trace width).
    """

    id: int
    room1: ExpansionRoom
    room2: ExpansionRoom
    position: FloatPoint         # Midpoint of shared boundary
    width: float                 # Available width through this door

    # Maze search state
    reached: bool = False
    cost: float = float('inf')
    reached_from: ExpansionRoom | None = None

    def other_room(self, room: ExpansionRoom) -> ExpansionRoom:
        """Get the room on the other side of this door."""
        return self.room2 if room.id == self.room1.id else self.room1

    def reset_search_state(self):
        self.reached = False
        self.cost = float('inf')
        self.reached_from = None


@dataclass
class ExpansionDrill:
    """A vertical connection between expansion rooms on different layers.

    Represents a location where a via can be placed to transition between
    layers. The drill connects rooms on adjacent layers at a specific position.
    """

    id: int
    position: IntPoint           # Center of the drill/via location
    rooms: list[ExpansionRoom] = field(default_factory=list)  # Connected rooms (one per layer)
    diameter: int = 0            # Required via diameter at this location

    # Maze search state
    reached: bool = False
    cost: float = float('inf')
    reached_from_room: ExpansionRoom | None = None

    def get_room_on_layer(self, layer_index: int) -> ExpansionRoom | None:
        for room in self.rooms:
            if room.layer_index == layer_index:
                return room
        return None

    def reset_search_state(self):
        self.reached = False
        self.cost = float('inf')
        self.reached_from_room = None


@dataclass
class ExpansionRoomGraph:
    """The complete graph of expansion rooms, doors, and drills.

    Built by the AutorouteEngine before each maze search. Divides the
    board's free space into non-overlapping convex tiles connected by
    doors and drills.
    """

    rooms: list[ExpansionRoom] = field(default_factory=list)
    doors: list[ExpansionDoor] = field(default_factory=list)
    drills: list[ExpansionDrill] = field(default_factory=list)

    _next_room_id: int = 0
    _next_door_id: int = 0
    _next_drill_id: int = 0

    def create_room(self, shape: TileShape, layer_index: int,
                    room_type: RoomType = RoomType.FREE_SPACE,
                    blocking_item: Item | None = None) -> ExpansionRoom:
        """Create and add a new expansion room."""
        room = ExpansionRoom(
            id=self._next_room_id,
            shape=shape,
            layer_index=layer_index,
            room_type=room_type,
            blocking_item=blocking_item,
        )
        self._next_room_id += 1
        self.rooms.append(room)
        return room

    def create_door(self, room1: ExpansionRoom, room2: ExpansionRoom,
                    position: FloatPoint, width: float) -> ExpansionDoor:
        """Create a door connecting two rooms on the same layer."""
        door = ExpansionDoor(
            id=self._next_door_id,
            room1=room1,
            room2=room2,
            position=position,
            width=width,
        )
        self._next_door_id += 1
        self.doors.append(door)
        room1.doors.append(door)
        room2.doors.append(door)
        return door

    def create_drill(self, position: IntPoint, rooms: list[ExpansionRoom],
                     diameter: int) -> ExpansionDrill:
        """Create a drill connecting rooms on different layers."""
        drill = ExpansionDrill(
            id=self._next_drill_id,
            position=position,
            rooms=rooms,
            diameter=diameter,
        )
        self._next_drill_id += 1
        self.drills.append(drill)
        for room in rooms:
            room.drills.append(drill)
        return drill

    def get_rooms_on_layer(self, layer_index: int) -> list[ExpansionRoom]:
        return [r for r in self.rooms if r.layer_index == layer_index]

    def get_room_at_point(self, point: IntPoint, layer_index: int) -> ExpansionRoom | None:
        """Find the room containing a point on a given layer."""
        for room in self.rooms:
            if room.layer_index == layer_index and room.contains_point(point):
                return room
        return None

    def reset_search_state(self):
        """Clear all search markers for a new maze search."""
        for room in self.rooms:
            room.reset_search_state()
        for door in self.doors:
            door.reset_search_state()
        for drill in self.drills:
            drill.reset_search_state()

    @property
    def room_count(self) -> int:
        return len(self.rooms)

    @property
    def door_count(self) -> int:
        return len(self.doors)
