"""
PullTightAlgo — Post-route trace optimization.

After initial routing, traces often have unnecessary corners and
suboptimal paths. Pull-tight optimization straightens traces by:
1. Removing redundant corners (collinear points)
2. Moving corners to shorten total trace length
3. Shortcutting corners when the direct path is obstacle-free

Uses real segment-level collision detection (not just bounding boxes).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.collision import expanded_segment_intersects_items
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.datastructures.search_tree import SearchTree
from kicad_autorouter.utils.timing import TimeLimit

logger = logging.getLogger(__name__)


@dataclass
class PullTightConfig:
    """Configuration for pull-tight optimization."""
    max_iterations: int = 10
    min_improvement: float = 100  # nm
    time_limit_seconds: float = 60.0


class PullTightAlgo:
    """Optimize trace paths by removing unnecessary corners and shortening paths.

    Uses segment-level collision detection for accurate shortcut validation.
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        config: PullTightConfig | None = None,
    ):
        self.board = board
        self.rules = rules
        self.config = config or PullTightConfig()
        self.search_tree = SearchTree(board.bounding_box)
        self._rebuild_tree()

    def _rebuild_tree(self):
        items = list(self.board.all_items())
        self.search_tree.rebuild(items, self.board.bounding_box)

    def optimize_all(self) -> int:
        """Optimize all traces. Returns number improved."""
        time_limit = TimeLimit(self.config.time_limit_seconds)
        improved_count = 0

        traces = self.board.get_traces()
        for trace in traces:
            if time_limit.is_expired():
                break
            if trace.is_fixed:
                continue
            if self._optimize_trace(trace):
                improved_count += 1

        logger.info("Pull-tight optimized %d/%d traces", improved_count, len(traces))
        return improved_count

    def _optimize_trace(self, trace: Trace) -> bool:
        if len(trace.corners) < 3:
            return False

        original_length = trace.total_length()
        best_corners = trace.corners[:]
        improved = False

        for _ in range(self.config.max_iterations):
            new_corners = self._pull_tight_pass(best_corners, trace)
            if new_corners is None or len(new_corners) < 2:
                break

            new_length = self._path_length(new_corners)
            if original_length - new_length < self.config.min_improvement:
                break

            best_corners = new_corners
            original_length = new_length
            improved = True

        if improved:
            trace.corners = best_corners

        return improved

    def _pull_tight_pass(
        self, corners: list[IntPoint], trace: Trace,
    ) -> list[IntPoint] | None:
        if len(corners) < 3:
            return None

        result = [corners[0]]
        i = 0
        while i < len(corners) - 1:
            if i + 2 < len(corners):
                if self._can_shortcut(
                    result[-1], corners[i + 2],
                    trace.layer_index, trace.width, trace.net_code,
                ):
                    result.append(corners[i + 2])
                    i += 2
                    continue

            result.append(corners[i + 1])
            i += 1

        result = self._remove_collinear(result)
        return result

    def _can_shortcut(
        self,
        from_pt: IntPoint,
        to_pt: IntPoint,
        layer_index: int,
        width: int,
        net_code: int,
    ) -> bool:
        """Check if a direct segment is obstacle-free using real collision detection."""
        half_width = width // 2
        clearance = self.rules.min_clearance

        # Get candidate items from spatial index
        from kicad_autorouter.geometry.shape import BoundingBox
        query_bb = BoundingBox(
            min(from_pt.x, to_pt.x) - half_width - clearance,
            min(from_pt.y, to_pt.y) - half_width - clearance,
            max(from_pt.x, to_pt.x) + half_width + clearance,
            max(from_pt.y, to_pt.y) + half_width + clearance,
        )
        candidates = self.search_tree.query_region(query_bb)

        return not expanded_segment_intersects_items(
            from_pt, to_pt,
            half_width, clearance,
            layer_index, net_code,
            candidates,
        )

    @staticmethod
    def _remove_collinear(corners: list[IntPoint]) -> list[IntPoint]:
        if len(corners) <= 2:
            return corners

        result = [corners[0]]
        for i in range(1, len(corners) - 1):
            prev = result[-1]
            curr = corners[i]
            next_pt = corners[i + 1]

            cross = ((curr.x - prev.x) * (next_pt.y - prev.y) -
                     (curr.y - prev.y) * (next_pt.x - prev.x))
            if abs(cross) > 100:
                result.append(curr)

        result.append(corners[-1])
        return result

    @staticmethod
    def _path_length(corners: list[IntPoint]) -> float:
        total = 0.0
        for i in range(len(corners) - 1):
            total += corners[i].distance_to(corners[i + 1])
        return total
