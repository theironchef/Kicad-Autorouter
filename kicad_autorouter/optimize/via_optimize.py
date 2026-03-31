"""
ViaOptimizer - Reduce unnecessary vias and optimize via placement.

After autorouting, vias may be:
- Redundant: layer transitions where a single-layer path exists
- Suboptimal: placed at grid points rather than trace intersections
- Relocatable: can be moved to shorten overall trace length

This optimizer handles removal, relocation, and type selection.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.geometry.collision import expanded_segment_intersects_items
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.via import Via
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.datastructures.search_tree import SearchTree

logger = logging.getLogger(__name__)


@dataclass
class ViaOptConfig:
    """Configuration for via optimization."""
    remove_redundant: bool = True
    relocate_vias: bool = True
    max_relocation_distance: int = 1_000_000  # 1mm max move
    relocation_grid: int = 50_000  # 50μm search grid


class ViaOptimizer:
    """Remove redundant vias, relocate for shorter traces, and optimize types."""

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        config: ViaOptConfig | None = None,
    ):
        self.board = board
        self.rules = rules
        self.config = config or ViaOptConfig()

    def optimize_all(self) -> int:
        """Run all via optimizations. Returns total improvements made."""
        improvements = 0

        if self.config.remove_redundant:
            improvements += self._remove_redundant()

        if self.config.relocate_vias:
            improvements += self._relocate_vias()

        return improvements

    def _remove_redundant(self) -> int:
        """Remove redundant vias. Returns number of vias removed."""
        removed = 0
        vias = self.board.get_vias()

        for via in vias:
            if via.is_fixed:
                continue
            if self._is_via_redundant(via):
                self.board.remove_item(via.id)
                removed += 1
                logger.debug("Removed redundant via at %s", via.position)

        if removed:
            logger.info("Via optimization removed %d/%d vias", removed, len(vias))
        return removed

    def _is_via_redundant(self, via: Via) -> bool:
        """Check if a via can be removed without breaking connectivity.

        A via is redundant if all traces connecting to it are on the same layer,
        or if the via connects fewer than 2 traces.
        """
        net_code = via.net_code
        if net_code <= 0:
            return False

        traces = self.board.get_traces_on_net(net_code)
        connecting_traces: list[Trace] = []

        for trace in traces:
            if trace.first_corner is None or trace.last_corner is None:
                continue
            r_sq = via.radius ** 2
            if (trace.first_corner.distance_squared(via.position) <= r_sq or
                    trace.last_corner.distance_squared(via.position) <= r_sq):
                connecting_traces.append(trace)

        if len(connecting_traces) < 2:
            return False

        layers = {t.layer_index for t in connecting_traces}
        if len(layers) == 1:
            return True

        return False

    def _relocate_vias(self) -> int:
        """Try to move vias to positions that shorten total trace length.

        For each via, checks nearby positions on a grid. If a position
        reduces the sum of connected trace lengths AND is clear of
        obstacles, the via is moved there.
        """
        relocated = 0
        vias = self.board.get_vias()
        search_tree = SearchTree(self.board.bounding_box)
        items = list(self.board.all_items())
        search_tree.rebuild(items, self.board.bounding_box)

        for via in vias:
            if via.is_fixed:
                continue

            new_pos = self._find_better_position(via, search_tree)
            if new_pos is not None:
                self._move_via(via, new_pos)
                relocated += 1
                # Rebuild search tree since positions changed
                items = list(self.board.all_items())
                search_tree.rebuild(items, self.board.bounding_box)

        if relocated:
            logger.info("Via relocation moved %d/%d vias", relocated, len(vias))
        return relocated

    def _find_better_position(
        self, via: Via, search_tree: SearchTree,
    ) -> IntPoint | None:
        """Search nearby positions for a better via location."""
        net_code = via.net_code
        if net_code <= 0:
            return None

        # Find trace endpoints connecting to this via
        connecting_points = self._get_connecting_endpoints(via)
        if len(connecting_points) < 2:
            return None

        current_cost = self._via_position_cost(via.position, connecting_points)
        best_cost = current_cost
        best_pos = None

        grid = self.config.relocation_grid
        max_dist = self.config.max_relocation_distance
        steps = max_dist // grid

        for dx_step in range(-steps, steps + 1):
            for dy_step in range(-steps, steps + 1):
                if dx_step == 0 and dy_step == 0:
                    continue
                candidate = IntPoint(
                    via.position.x + dx_step * grid,
                    via.position.y + dy_step * grid,
                )

                cost = self._via_position_cost(candidate, connecting_points)
                if cost >= best_cost:
                    continue

                # Validate: no collision at this position
                clearance = self.rules.min_clearance
                if self._via_position_clear(
                    candidate, via, clearance, search_tree,
                ):
                    best_cost = cost
                    best_pos = candidate

        return best_pos

    def _get_connecting_endpoints(self, via: Via) -> list[IntPoint]:
        """Get trace endpoints that connect to this via (the 'other' end)."""
        net_code = via.net_code
        traces = self.board.get_traces_on_net(net_code)
        points = []
        r_sq = via.radius ** 2

        for trace in traces:
            if trace.first_corner is None or trace.last_corner is None:
                continue
            if trace.first_corner.distance_squared(via.position) <= r_sq:
                points.append(trace.last_corner)
            elif trace.last_corner.distance_squared(via.position) <= r_sq:
                points.append(trace.first_corner)

        return points

    @staticmethod
    def _via_position_cost(pos: IntPoint, endpoints: list[IntPoint]) -> float:
        """Cost of a via position = sum of distances to connected endpoints."""
        return sum(
            math.sqrt(pos.distance_squared(ep)) for ep in endpoints
        )

    def _via_position_clear(
        self,
        pos: IntPoint,
        via: Via,
        clearance: int,
        search_tree: SearchTree,
    ) -> bool:
        """Check if a via position is free of collisions."""
        expand = via.radius + clearance
        query_bb = BoundingBox(
            pos.x - expand, pos.y - expand,
            pos.x + expand, pos.y + expand,
        )
        candidates = search_tree.query_region(query_bb)

        for item in candidates:
            if item.id == via.id:
                continue
            if via.net_code in item.net_codes:
                continue

            for layer_idx in range(via.start_layer, via.end_layer + 1):
                if not item.is_on_layer(layer_idx):
                    continue
                item_shape = item.get_shape_on_layer(layer_idx)
                if item_shape is None:
                    continue

                item_bb = item.bounding_box()
                expanded_bb = item_bb.enlarge(clearance)
                if expanded_bb.contains(pos):
                    return False

        # Check board bounds
        board_bb = self.board.bounding_box
        if (pos.x - via.radius < board_bb.x_min or
                pos.y - via.radius < board_bb.y_min or
                pos.x + via.radius > board_bb.x_max or
                pos.y + via.radius > board_bb.y_max):
            return False

        return True

    def _move_via(self, via: Via, new_pos: IntPoint):
        """Move a via to a new position and update connected trace endpoints."""
        old_pos = via.position
        r_sq = via.radius ** 2

        # Update trace endpoints that connect to this via
        traces = self.board.get_traces_on_net(via.net_code)
        for trace in traces:
            if trace.first_corner and trace.first_corner.distance_squared(old_pos) <= r_sq:
                trace.corners[0] = new_pos
            if trace.last_corner and trace.last_corner.distance_squared(old_pos) <= r_sq:
                trace.corners[-1] = new_pos

        via.position = new_pos
        logger.debug("Relocated via from %s to %s", old_pos, new_pos)
