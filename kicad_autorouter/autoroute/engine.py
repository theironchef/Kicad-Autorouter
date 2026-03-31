"""
AutorouteEngine — Builds expansion room graphs and manages routing state.

The engine is responsible for:
1. Partitioning free board space into expansion rooms (convex tiles)
2. Finding doors between adjacent rooms
3. Identifying drill locations for layer transitions
4. Coordinating the maze search for a single net connection
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from kicad_autorouter.geometry.point import FloatPoint, IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.geometry.octagon import IntOctagon
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.item import Item
from kicad_autorouter.board.pad import Pad
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.via import Via
from kicad_autorouter.board.obstacle import ObstacleArea
from kicad_autorouter.autoroute.expansion import (
    ExpansionDoor, ExpansionDrill, ExpansionRoom, ExpansionRoomGraph, RoomType,
)
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.datastructures.search_tree import SearchTree

logger = logging.getLogger(__name__)

# Grid granularity for drill page (controls via placement density)
_DRILL_PAGE_SIZE = 500_000  # 0.5mm grid for potential via locations


@dataclass
class AutorouteEngine:
    """Builds and manages expansion room graphs for autorouting.

    For each net connection to route, the engine:
    1. Computes free space around obstacles on each layer
    2. Partitions free space into convex tiles (expansion rooms)
    3. Finds connections between adjacent rooms (doors)
    4. Identifies via placement opportunities (drills)
    5. Hands the graph to MazeSearchAlgo for pathfinding
    """

    board: RoutingBoard
    rules: DesignRules
    search_tree: SearchTree = field(default_factory=SearchTree)
    _graph: ExpansionRoomGraph = field(default_factory=ExpansionRoomGraph)

    def __post_init__(self):
        """Initialize search tree from board items."""
        self._rebuild_search_tree()

    def _rebuild_search_tree(self):
        """Rebuild spatial index from current board state."""
        items = list(self.board.all_items())
        self.search_tree.rebuild(items, self.board.bounding_box)

    def build_expansion_graph(
        self,
        source_pads: list[Pad],
        target_pads: list[Pad],
        net_code: int,
    ) -> ExpansionRoomGraph:
        """Build the expansion room graph for routing between source and target pads."""
        self._graph = ExpansionRoomGraph()
        net_class = self.board.get_net_class_for_net(net_code)
        clearance = self.rules.get_clearance(net_class)
        trace_half_width = self.rules.get_trace_cost_width(net_class)

        source_ids = {p.id for p in source_pads}
        target_ids = {p.id for p in target_pads}

        # Build rooms on each copper layer
        for layer in self.board.layer_structure.layers:
            self._build_layer_rooms(
                layer.index, net_code, clearance, trace_half_width,
                source_ids, target_ids,
            )

        # Build doors between adjacent rooms on same layer
        self._build_doors()

        # Build drills for layer transitions
        self._build_drills(net_code, net_class)

        logger.debug(
            "Built expansion graph: %d rooms, %d doors, %d drills",
            self._graph.room_count, self._graph.door_count, len(self._graph.drills),
        )
        return self._graph

    def _build_layer_rooms(
        self,
        layer_index: int,
        net_code: int,
        clearance: int,
        trace_half_width: int,
        source_ids: set[int],
        target_ids: set[int],
    ):
        """Build expansion rooms for a single layer.

        Strategy: divide board into grid cells. Each cell is either
        FREE_SPACE, OBSTACLE, or TARGET depending on what occupies it.
        Source pads get their grid cell marked as FREE_SPACE (routing starts there).
        Target pads get their grid cell marked as TARGET.
        """
        board_bb = self.board.bounding_box

        # Collect obstacles on this layer (items from OTHER nets)
        items_on_layer = self.board.get_items_on_layer(layer_index)
        obstacles: list[Item] = []
        for item in items_on_layer:
            if net_code in item.net_codes:
                continue  # Same-net items are NOT obstacles
            if isinstance(item, (Pad, Trace, Via, ObstacleArea)):
                obstacles.append(item)

        # Collect pad positions for source/target marking
        source_positions: list[IntPoint] = []
        target_positions: list[IntPoint] = []
        for item in items_on_layer:
            if isinstance(item, Pad) and item.is_on_layer(layer_index):
                if item.id in source_ids:
                    source_positions.append(item.position)
                elif item.id in target_ids:
                    target_positions.append(item.position)

        # Grid cell size — adaptive based on board size
        cell_size = max(
            board_bb.width // 20,
            board_bb.height // 20,
            1_000_000,  # Minimum 1mm cells
        )

        x = board_bb.x_min
        while x < board_bb.x_max:
            y = board_bb.y_min
            x_end = min(x + cell_size, board_bb.x_max)
            while y < board_bb.y_max:
                y_end = min(y + cell_size, board_bb.y_max)
                cell_bbox = BoundingBox(x, y, x_end, y_end)

                # Check if any obstacle overlaps this cell
                is_blocked = False
                blocking_item = None
                for obs in obstacles:
                    obs_bb = obs.bounding_box()
                    if obs_bb.intersects(cell_bbox):
                        is_blocked = True
                        blocking_item = obs
                        break

                shape = IntOctagon.from_bbox(x, y, x_end, y_end)

                if is_blocked:
                    self._graph.create_room(
                        shape, layer_index, RoomType.OBSTACLE, blocking_item
                    )
                else:
                    # Check if a target pad is inside this cell
                    is_target = any(
                        cell_bbox.contains(p) for p in target_positions
                    )
                    room_type = RoomType.TARGET if is_target else RoomType.FREE_SPACE
                    self._graph.create_room(shape, layer_index, room_type)

                y = y_end
            x = x_end

    def _build_doors(self):
        """Find doors between adjacent rooms on the same layer.

        Two rooms share a door if their tile shapes share a boundary segment
        (i.e., they are adjacent in the grid). Uses spatial lookup for O(n)
        performance instead of O(n²) pairwise comparison.
        """
        # Group rooms by layer
        rooms_by_layer: dict[int, list[ExpansionRoom]] = {}
        for room in self._graph.rooms:
            rooms_by_layer.setdefault(room.layer_index, []).append(room)

        for layer_idx, rooms in rooms_by_layer.items():
            # Build spatial lookup: (x_min, y_min) -> room for O(1) neighbor finding
            room_lookup: dict[tuple[int, int], ExpansionRoom] = {}
            for room in rooms:
                bb = room.shape.bounding_box()
                room_lookup[(bb.x_min, bb.y_min)] = room

            for room in rooms:
                if room.room_type == RoomType.OBSTACLE:
                    continue
                bb = room.shape.bounding_box()
                cell_w = bb.x_max - bb.x_min
                cell_h = bb.y_max - bb.y_min

                # Check right neighbor
                right = room_lookup.get((bb.x_max, bb.y_min))
                if right and right.room_type != RoomType.OBSTACLE:
                    obb = right.shape.bounding_box()
                    shared = min(bb.y_max, obb.y_max) - max(bb.y_min, obb.y_min)
                    if shared > 0 and room.id < right.id:
                        mid = FloatPoint(
                            float(bb.x_max),
                            (max(bb.y_min, obb.y_min) + min(bb.y_max, obb.y_max)) / 2.0,
                        )
                        self._graph.create_door(room, right, mid, float(shared))

                # Check bottom neighbor
                below = room_lookup.get((bb.x_min, bb.y_max))
                if below and below.room_type != RoomType.OBSTACLE:
                    obb = below.shape.bounding_box()
                    shared = min(bb.x_max, obb.x_max) - max(bb.x_min, obb.x_min)
                    if shared > 0 and room.id < below.id:
                        mid = FloatPoint(
                            (max(bb.x_min, obb.x_min) + min(bb.x_max, obb.x_max)) / 2.0,
                            float(bb.y_max),
                        )
                        self._graph.create_door(room, below, mid, float(shared))

    def _build_drills(self, net_code: int, net_class):
        """Build drill locations for layer transitions."""
        if self.board.layer_structure.copper_layer_count < 2:
            return  # Single-layer board, no drills needed

        via_diameter = self.rules.get_via_diameter(net_class)
        board_bb = self.board.bounding_box

        grid_step = max(_DRILL_PAGE_SIZE, via_diameter * 2)
        x = board_bb.x_min + grid_step // 2
        while x < board_bb.x_max:
            y = board_bb.y_min + grid_step // 2
            while y < board_bb.y_max:
                pos = IntPoint(x, y)

                rooms_at_pos: list[ExpansionRoom] = []
                for layer in self.board.layer_structure.layers:
                    room = self._graph.get_room_at_point(pos, layer.index)
                    if room and room.is_free:
                        rooms_at_pos.append(room)

                if len(rooms_at_pos) >= 2:
                    self._graph.create_drill(pos, rooms_at_pos, via_diameter)

                y += grid_step
            x += grid_step

    @staticmethod
    def _shared_boundary_width(bb1: BoundingBox, bb2: BoundingBox) -> int:
        """Calculate the width of shared boundary between two adjacent bboxes."""
        # Check horizontal adjacency (shared vertical boundary)
        if bb1.x_max == bb2.x_min or bb2.x_max == bb1.x_min:
            overlap_min = max(bb1.y_min, bb2.y_min)
            overlap_max = min(bb1.y_max, bb2.y_max)
            if overlap_max > overlap_min:
                return overlap_max - overlap_min

        # Check vertical adjacency (shared horizontal boundary)
        if bb1.y_max == bb2.y_min or bb2.y_max == bb1.y_min:
            overlap_min = max(bb1.x_min, bb2.x_min)
            overlap_max = min(bb1.x_max, bb2.x_max)
            if overlap_max > overlap_min:
                return overlap_max - overlap_min

        return 0

    def update_after_route(self, net_code: int):
        """Update search tree after a route has been added."""
        self._rebuild_search_tree()
