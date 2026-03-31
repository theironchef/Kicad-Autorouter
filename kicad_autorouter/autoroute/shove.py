"""
ShoveTraceAlgo — Push existing traces aside to make room for new routing.

When the maze router encounters a congested area, the shove algorithm can
move existing traces laterally (perpendicular to their direction) to create
clearance for the new route. This is Freerouting's ShoveTraceAlgo concept.

The shove is validated before committing: the displaced trace must still
maintain clearance with all other board items. If validation fails, the
shove is rejected and the router tries a different path.

Shove order:
1. Identify conflicting trace segment
2. Compute minimum displacement to clear the new route
3. Move the conflicting segment perpendicular to its direction
4. Validate the shoved position against all obstacles
5. Recursively shove any newly-conflicting traces (up to max depth)
6. Commit or reject the shove chain
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.geometry.collision import (
    expanded_segment_intersects_items,
    segment_clearance_to_segment,
)
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.item import FixedState, Item
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.datastructures.search_tree import SearchTree

logger = logging.getLogger(__name__)


@dataclass
class ShoveConfig:
    """Configuration for the shove algorithm."""
    max_recursion_depth: int = 5       # Max nested shoves
    max_shove_distance: int = 2_000_000  # Max displacement (2mm)
    spring_over_enabled: bool = True   # Allow wrapping around obstacles


@dataclass
class ShoveResult:
    """Result of a shove attempt."""
    success: bool = False
    shoved_traces: list[tuple[int, Trace]] = None  # [(trace_id, new_trace), ...]
    shove_distance: int = 0

    def __post_init__(self):
        if self.shoved_traces is None:
            self.shoved_traces = []


class ShoveTraceAlgo:
    """Push traces aside to make room for new routing.

    This implements the core trace-shoving logic from Freerouting.
    When a new trace segment conflicts with an existing trace, it
    computes the minimum perpendicular displacement needed and
    validates the new position.
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        search_tree: SearchTree | None = None,
        config: ShoveConfig | None = None,
    ):
        self.board = board
        self.rules = rules
        self.config = config or ShoveConfig()
        self.search_tree = search_tree or SearchTree()
        if not search_tree:
            items = list(board.all_items())
            self.search_tree.rebuild(items, board.bounding_box)

    def shove_for_segment(
        self,
        new_p1: IntPoint,
        new_p2: IntPoint,
        new_half_width: int,
        new_net_code: int,
        layer_index: int,
    ) -> ShoveResult:
        """Try to shove traces that conflict with a proposed new segment.

        Args:
            new_p1, new_p2: The new trace segment that needs space.
            new_half_width: Half-width of the new trace.
            new_net_code: Net code of the new trace.
            layer_index: Layer of the new segment.

        Returns:
            ShoveResult with success=True and the list of shoved traces
            if all conflicts can be resolved, or success=False.
        """
        clearance = self.rules.min_clearance
        result = ShoveResult()

        # Find conflicting traces
        conflicts = self._find_conflicting_traces(
            new_p1, new_p2, new_half_width, clearance,
            new_net_code, layer_index,
        )

        if not conflicts:
            result.success = True
            return result

        return self._recursive_shove(
            new_p1, new_p2, new_half_width, new_net_code,
            layer_index, clearance, conflicts, depth=0,
        )

    def _find_conflicting_traces(
        self,
        p1: IntPoint,
        p2: IntPoint,
        half_width: int,
        clearance: int,
        net_code: int,
        layer_index: int,
    ) -> list[tuple[Trace, int]]:
        """Find trace segments that conflict with a proposed segment.

        Returns list of (trace, segment_index) pairs.
        """
        total_expand = half_width + clearance
        query_bb = BoundingBox(
            min(p1.x, p2.x) - total_expand,
            min(p1.y, p2.y) - total_expand,
            max(p1.x, p2.x) + total_expand,
            max(p1.y, p2.y) + total_expand,
        )
        candidates = self.search_tree.query_region(query_bb)

        conflicts = []
        for item in candidates:
            if not isinstance(item, Trace):
                continue
            if net_code in item.net_codes:
                continue
            if not item.is_on_layer(layer_index):
                continue
            # Don't skip fixed traces here — they ARE conflicts.
            # _recursive_shove will reject them.

            # Check each segment of this trace
            for seg_idx in range(item.segment_count):
                s1 = item.corners[seg_idx]
                s2 = item.corners[seg_idx + 1]
                required_gap = half_width + item.half_width + clearance
                dist = segment_clearance_to_segment(p1, p2, s1, s2)
                if dist < required_gap:
                    conflicts.append((item, seg_idx))
                    break  # One conflict per trace is enough

        return conflicts

    def _recursive_shove(
        self,
        new_p1: IntPoint,
        new_p2: IntPoint,
        new_half_width: int,
        new_net_code: int,
        layer_index: int,
        clearance: int,
        conflicts: list[tuple[Trace, int]],
        depth: int,
    ) -> ShoveResult:
        """Recursively shove conflicting traces."""
        if depth > self.config.max_recursion_depth:
            logger.debug("Shove recursion depth exceeded")
            return ShoveResult(success=False)

        all_shoves: list[tuple[int, Trace]] = []

        for trace, seg_idx in conflicts:
            if trace.fixed_state in (FixedState.USER_FIXED, FixedState.SYSTEM_FIXED):
                return ShoveResult(success=False)

            # Compute shove direction and distance
            shove = self._compute_shove_vector(
                new_p1, new_p2, new_half_width,
                trace, seg_idx, clearance,
            )
            if shove is None:
                return ShoveResult(success=False)

            dx, dy = shove

            # Check distance limit
            dist = int(math.sqrt(dx * dx + dy * dy))
            if dist > self.config.max_shove_distance:
                logger.debug("Shove distance %d exceeds limit %d",
                             dist, self.config.max_shove_distance)
                return ShoveResult(success=False)

            # Create shoved trace
            shoved_trace = trace.translate_segment(seg_idx, dx, dy)

            # Validate: shoved trace must not violate clearance with other items
            if not self._validate_shoved_trace(
                shoved_trace, seg_idx, clearance, new_net_code,
            ):
                # Try spring-over (opposite direction)
                if self.config.spring_over_enabled:
                    shoved_trace = trace.translate_segment(seg_idx, -dx, -dy)
                    if not self._validate_shoved_trace(
                        shoved_trace, seg_idx, clearance, new_net_code,
                    ):
                        return ShoveResult(success=False)
                else:
                    return ShoveResult(success=False)

            all_shoves.append((trace.id, shoved_trace))

        return ShoveResult(
            success=True,
            shoved_traces=all_shoves,
            shove_distance=dist if conflicts else 0,
        )

    def _compute_shove_vector(
        self,
        new_p1: IntPoint,
        new_p2: IntPoint,
        new_half_width: int,
        trace: Trace,
        seg_idx: int,
        clearance: int,
    ) -> tuple[int, int] | None:
        """Compute the displacement vector to shove a trace segment clear.

        The shove direction is perpendicular to the conflicting segment.
        The shove distance is enough to provide full clearance.
        """
        s1 = trace.corners[seg_idx]
        s2 = trace.corners[seg_idx + 1]

        # Segment direction vector
        seg_dx = s2.x - s1.x
        seg_dy = s2.y - s1.y
        seg_len = math.sqrt(seg_dx * seg_dx + seg_dy * seg_dy)

        if seg_len == 0:
            return None

        # Perpendicular direction (normalized)
        perp_x = -seg_dy / seg_len
        perp_y = seg_dx / seg_len

        # Determine which side to push: away from the new segment's midpoint
        new_mid = IntPoint((new_p1.x + new_p2.x) // 2, (new_p1.y + new_p2.y) // 2)
        seg_mid = IntPoint((s1.x + s2.x) // 2, (s1.y + s2.y) // 2)

        # Dot product to determine which perpendicular direction is away
        to_seg = (seg_mid.x - new_mid.x, seg_mid.y - new_mid.y)
        dot = to_seg[0] * perp_x + to_seg[1] * perp_y
        if dot < 0:
            perp_x, perp_y = -perp_x, -perp_y

        # Required clearance distance
        required_gap = new_half_width + trace.half_width + clearance
        current_dist = segment_clearance_to_segment(new_p1, new_p2, s1, s2)
        shove_amount = required_gap - current_dist + 1000  # 1μm margin

        if shove_amount <= 0:
            return (0, 0)

        dx = round(perp_x * shove_amount)
        dy = round(perp_y * shove_amount)

        return (dx, dy)

    def _validate_shoved_trace(
        self,
        shoved_trace: Trace,
        seg_idx: int,
        clearance: int,
        new_net_code: int,
    ) -> bool:
        """Validate that a shoved trace doesn't violate clearance.

        Checks the shoved segment (and its two neighbors) against all
        other board items except same-net items and the new route.
        """
        # Check segments around the shoved area
        start_seg = max(0, seg_idx - 1)
        end_seg = min(shoved_trace.segment_count, seg_idx + 2)

        for si in range(start_seg, end_seg):
            p1 = shoved_trace.corners[si]
            p2 = shoved_trace.corners[si + 1]

            query_bb = BoundingBox(
                min(p1.x, p2.x) - shoved_trace.half_width - clearance,
                min(p1.y, p2.y) - shoved_trace.half_width - clearance,
                max(p1.x, p2.x) + shoved_trace.half_width + clearance,
                max(p1.y, p2.y) + shoved_trace.half_width + clearance,
            )
            candidates = self.search_tree.query_region(query_bb)

            # Filter out the trace being shoved and new-net items
            filtered = [
                item for item in candidates
                if item.id != shoved_trace.id
                and new_net_code not in item.net_codes
            ]

            if expanded_segment_intersects_items(
                p1, p2,
                shoved_trace.half_width, clearance,
                shoved_trace.layer_index,
                shoved_trace.net_code,
                filtered,
            ):
                return False

        # Check the shoved trace stays within board bounds
        bb = shoved_trace.bounding_box()
        board_bb = self.board.bounding_box
        if (bb.x_min < board_bb.x_min or bb.y_min < board_bb.y_min or
                bb.x_max > board_bb.x_max or bb.y_max > board_bb.y_max):
            return False

        return True

    def apply_shoves(self, result: ShoveResult):
        """Apply validated shove results to the board.

        Replaces the original traces with their shoved versions.
        """
        if not result.success:
            return

        for trace_id, shoved_trace in result.shoved_traces:
            if trace_id in self.board._items:
                self.board._items[trace_id] = shoved_trace

        # Rebuild search tree after modifications
        items = list(self.board.all_items())
        self.search_tree.rebuild(items, self.board.bounding_box)
