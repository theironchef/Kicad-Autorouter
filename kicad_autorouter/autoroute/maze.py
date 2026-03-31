"""
MazeSearchAlgo - Priority-queue A*-like pathfinding through expansion rooms.

The maze search is the heart of the autorouter. It expands outward from
source pads through expansion room doors and drills, using a priority queue
ordered by estimated total cost (cost-so-far + heuristic-to-target).

This implements the same algorithm as Freerouting's MazeSearchAlgo:
- Expand from source rooms through doors to adjacent free rooms
- Expand through drills to rooms on other layers (via insertion)
- Track cost including trace length, via penalties, and direction changes
- Stop when a target room is reached
- Backtrace from target to source to recover the path
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum, auto

from kicad_autorouter.geometry.point import FloatPoint, IntPoint
from kicad_autorouter.autoroute.expansion import (
    ExpansionDoor, ExpansionDrill, ExpansionRoom, ExpansionRoomGraph, RoomType,
)
from kicad_autorouter.board.pad import Pad
from kicad_autorouter.datastructures.priority_queue import MazePriorityQueue
from kicad_autorouter.utils.timing import TimeLimit

logger = logging.getLogger(__name__)


class SearchState(Enum):
    """Result of a maze search attempt."""
    NOT_STARTED = auto()
    IN_PROGRESS = auto()
    FOUND = auto()           # Path found to target
    NOT_FOUND = auto()       # All reachable rooms explored, no path
    TIME_EXPIRED = auto()    # Time limit reached


@dataclass
class MazeSearchResult:
    """Result of a completed maze search."""

    state: SearchState
    path_rooms: list[ExpansionRoom] = field(default_factory=list)
    path_doors: list[ExpansionDoor] = field(default_factory=list)
    path_drills: list[ExpansionDrill] = field(default_factory=list)
    total_cost: float = float('inf')

    # Path as waypoints (for trace generation)
    waypoints: list[IntPoint] = field(default_factory=list)
    waypoint_layers: list[int] = field(default_factory=list)


@dataclass
class _SearchElement:
    """An element in the maze search priority queue."""
    room: ExpansionRoom
    via_door: ExpansionDoor | None = None   # Door used to enter room (None if via drill)
    via_drill: ExpansionDrill | None = None  # Drill used to enter room (None if via door)
    cost: float = 0.0
    estimated_total: float = 0.0


class MazeSearchAlgo:
    """A*-like maze search through expansion rooms.

    Finds the lowest-cost path from source rooms to target rooms,
    expanding through doors (same-layer) and drills (layer change).
    """

    # Cost factors
    TRACE_COST_PER_NM = 1.0          # Cost per nanometer of trace
    VIA_COST = 50_000_000.0          # Fixed cost for placing a via
    DIRECTION_CHANGE_COST = 5_000.0   # Penalty for changing direction
    OBSTACLE_PROXIMITY_COST = 1_000.0 # Extra cost near obstacles
    RIPUP_COST_FACTOR = 100_000_000.0 # Cost for ripping up existing trace

    def __init__(
        self,
        graph: ExpansionRoomGraph,
        source_pads: list[Pad],
        target_pads: list[Pad],
        time_limit: TimeLimit | None = None,
        ripup_cost_multiplier: float = 1.0,
    ):
        self.graph = graph
        self.source_pads = source_pads
        self.target_pads = target_pads
        self.time_limit = time_limit or TimeLimit()
        self.ripup_cost_multiplier = ripup_cost_multiplier
        self._pq = MazePriorityQueue()

    def find_connection(self) -> MazeSearchResult:
        """Run the maze search. Returns the result with path if found."""
        self.graph.reset_search_state()

        # Initialize: push source rooms into priority queue
        target_centers = self._compute_target_centers()
        source_rooms = self._find_source_rooms()
        target_rooms = self._find_target_rooms()

        if not source_rooms or not target_rooms:
            logger.debug("No source or target rooms found")
            return MazeSearchResult(state=SearchState.NOT_FOUND)

        # Push source rooms
        for room in source_rooms:
            heuristic = self._estimate_to_target(room.center, target_centers)
            elem = _SearchElement(room=room, cost=0.0, estimated_total=heuristic)
            self._pq.push(heuristic, elem)
            room.reached = True
            room.cost_to_here = 0.0

        # Main search loop
        found_room: ExpansionRoom | None = None

        while not self._pq.is_empty:
            if self.time_limit.is_expired():
                return MazeSearchResult(state=SearchState.TIME_EXPIRED)

            cost, elem = self._pq.pop()
            if cost is None:
                break

            current = elem.room

            # Check if we reached a target
            if current.is_target or current in target_rooms:
                found_room = current
                break

            # Expand through doors to adjacent rooms
            for door in current.doors:
                neighbor = door.other_room(current)
                if neighbor.room_type == RoomType.OBSTACLE:
                    continue

                # Calculate cost through this door
                door_cost = self._door_traversal_cost(current, door, neighbor)
                new_cost = current.cost_to_here + door_cost

                if new_cost < neighbor.cost_to_here:
                    neighbor.cost_to_here = new_cost
                    neighbor.reached = True
                    neighbor.reached_from_door = door

                    heuristic = self._estimate_to_target(neighbor.center, target_centers)
                    total_est = new_cost + heuristic

                    new_elem = _SearchElement(
                        room=neighbor, via_door=door,
                        cost=new_cost, estimated_total=total_est,
                    )
                    self._pq.push(total_est, new_elem)

            # Expand through drills to other layers
            for drill in current.drills:
                for other_room in drill.rooms:
                    if other_room.id == current.id:
                        continue
                    if other_room.room_type == RoomType.OBSTACLE:
                        continue

                    drill_cost = self.VIA_COST * self.ripup_cost_multiplier
                    new_cost = current.cost_to_here + drill_cost

                    if new_cost < other_room.cost_to_here:
                        other_room.cost_to_here = new_cost
                        other_room.reached = True
                        other_room.reached_from_drill = drill
                        drill.reached_from_room = current

                        heuristic = self._estimate_to_target(
                            other_room.center, target_centers
                        )
                        total_est = new_cost + heuristic

                        new_elem = _SearchElement(
                            room=other_room, via_drill=drill,
                            cost=new_cost, estimated_total=total_est,
                        )
                        self._pq.push(total_est, new_elem)

        if found_room is None:
            return MazeSearchResult(state=SearchState.NOT_FOUND)

        # Backtrace to build path
        return self._backtrace(found_room, source_rooms)

    def _find_source_rooms(self) -> list[ExpansionRoom]:
        """Find rooms containing source pads."""
        rooms = []
        for pad in self.source_pads:
            for layer_idx in pad.layer_indices:
                room = self.graph.get_room_at_point(pad.position, layer_idx)
                if room:
                    rooms.append(room)
        return rooms

    def _find_target_rooms(self) -> list[ExpansionRoom]:
        """Find rooms containing target pads (mark them as TARGET)."""
        rooms = []
        for pad in self.target_pads:
            for layer_idx in pad.layer_indices:
                room = self.graph.get_room_at_point(pad.position, layer_idx)
                if room:
                    room.room_type = RoomType.TARGET
                    rooms.append(room)
        return rooms

    def _compute_target_centers(self) -> list[FloatPoint]:
        """Get center positions of all target pads."""
        return [FloatPoint(float(p.position.x), float(p.position.y))
                for p in self.target_pads]

    def _estimate_to_target(self, pos: FloatPoint, targets: list[FloatPoint]) -> float:
        """Heuristic: minimum Manhattan distance to any target."""
        if not targets:
            return 0.0
        return min(
            (abs(pos.x - t.x) + abs(pos.y - t.y)) * self.TRACE_COST_PER_NM
            for t in targets
        )

    def _door_traversal_cost(
        self,
        from_room: ExpansionRoom,
        door: ExpansionDoor,
        to_room: ExpansionRoom,
    ) -> float:
        """Calculate cost of traversing a door from one room to another."""
        # Base cost: distance between room centers
        c1 = from_room.center
        c2 = to_room.center
        distance = math.sqrt((c1.x - c2.x) ** 2 + (c1.y - c2.y) ** 2)
        cost = distance * self.TRACE_COST_PER_NM

        # Penalty for narrow doors (tight spaces)
        if door.width > 0:
            # Higher cost for tighter passages
            cost *= max(1.0, 100_000.0 / door.width)

        # Penalty for obstacle rooms
        if to_room.room_type == RoomType.OBSTACLE:
            cost += self.RIPUP_COST_FACTOR * self.ripup_cost_multiplier

        return cost

    def _backtrace(
        self,
        target_room: ExpansionRoom,
        source_rooms: list[ExpansionRoom],
    ) -> MazeSearchResult:
        """Trace back from target to source to build the path."""
        path_rooms: list[ExpansionRoom] = []
        path_doors: list[ExpansionDoor] = []
        path_drills: list[ExpansionDrill] = []
        waypoints: list[IntPoint] = []
        waypoint_layers: list[int] = []

        current = target_room
        source_ids = {r.id for r in source_rooms}

        while current is not None:
            path_rooms.append(current)
            center = current.center
            waypoints.append(IntPoint(round(center.x), round(center.y)))
            waypoint_layers.append(current.layer_index)

            if current.id in source_ids:
                break

            if current.reached_from_door:
                path_doors.append(current.reached_from_door)
                current = current.reached_from_door.other_room(current)
            elif current.reached_from_drill:
                path_drills.append(current.reached_from_drill)
                # Find the room the drill came from
                drill = current.reached_from_drill
                prev = drill.reached_from_room
                if prev is None:
                    # Find source room of this drill
                    for r in drill.rooms:
                        if r.id != current.id and r.reached:
                            prev = r
                            break
                current = prev
            else:
                break

        # Reverse to get source-to-target order
        path_rooms.reverse()
        path_doors.reverse()
        path_drills.reverse()
        waypoints.reverse()
        waypoint_layers.reverse()

        return MazeSearchResult(
            state=SearchState.FOUND,
            path_rooms=path_rooms,
            path_doors=path_doors,
            path_drills=path_drills,
            total_cost=target_room.cost_to_here,
            waypoints=waypoints,
            waypoint_layers=waypoint_layers,
        )
