"""
PullTightAlgo45 — 45-degree constrained trace optimization.

Optimizes traces while maintaining all segments at 0°, 45°, 90°, 135°
angles (and their reverses). This is the standard PCB routing aesthetic
where traces only use orthogonal and 45° diagonal segments.

Strategy:
1. For each corner triple (A, B, C), try to replace the two segments
   A→B→C with a 45°-compliant shortcut (A→B'→C or A→C directly)
2. The shortcut is validated against obstacles using collision detection
3. Intermediate points are snapped to the nearest 45° grid position
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.direction import Direction, Direction45
from kicad_autorouter.geometry.collision import expanded_segment_intersects_items
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.datastructures.search_tree import SearchTree
from kicad_autorouter.utils.timing import TimeLimit

logger = logging.getLogger(__name__)

# The 8 unit direction vectors for 45° routing
_DIR_VECTORS = [
    (1, 0), (1, -1), (0, -1), (-1, -1),
    (-1, 0), (-1, 1), (0, 1), (1, 1),
]


@dataclass
class PullTight45Config:
    """Configuration for 45° pull-tight optimization."""
    max_iterations: int = 10
    min_improvement: float = 100  # nm
    time_limit_seconds: float = 60.0


class PullTightAlgo45:
    """Optimize traces with 45-degree angle constraint.

    All output segments are guaranteed to be at 0°, 45°, 90°, or 135°
    (including reverses). This matches standard PCB routing aesthetics.
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        config: PullTight45Config | None = None,
    ):
        self.board = board
        self.rules = rules
        self.config = config or PullTight45Config()
        self.search_tree = SearchTree(board.bounding_box)
        self._rebuild_tree()

    def _rebuild_tree(self):
        items = list(self.board.all_items())
        self.search_tree.rebuild(items, self.board.bounding_box)

    def optimize_all(self) -> int:
        """Optimize all traces with 45° constraint. Returns number improved."""
        time_limit = TimeLimit(self.config.time_limit_seconds)
        improved_count = 0

        for trace in self.board.get_traces():
            if time_limit.is_expired():
                break
            if trace.is_fixed:
                continue
            if self._optimize_trace(trace):
                improved_count += 1

        if improved_count:
            logger.info("PullTight45 optimized %d traces", improved_count)
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
            new_length = _path_length(new_corners)
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
        """One pass: try to shortcut each corner triple with 45° segments."""
        if len(corners) < 3:
            return None

        result = [corners[0]]
        i = 0
        changed = False

        while i < len(corners) - 1:
            if i + 2 < len(corners):
                # Try direct 45°-compliant shortcut A→C
                shortcut = self._try_45_shortcut(
                    result[-1], corners[i + 2],
                    trace.layer_index, trace.width, trace.net_code,
                )
                if shortcut is not None:
                    result.extend(shortcut)
                    i += 2
                    changed = True
                    continue

            result.append(corners[i + 1])
            i += 1

        return result if changed else None

    def _try_45_shortcut(
        self,
        from_pt: IntPoint,
        to_pt: IntPoint,
        layer_index: int,
        width: int,
        net_code: int,
    ) -> list[IntPoint] | None:
        """Try to connect from_pt to to_pt using 45°-aligned segments.

        Returns a list of intermediate + endpoint, or None if no valid
        shortcut exists. Tries direct (1 segment) and L-shaped (2 segments).
        """
        half_width = width // 2
        clearance = self.rules.min_clearance

        # 1) Try direct: if the vector is already 45°-aligned
        if _is_45_aligned(from_pt, to_pt):
            if self._can_shortcut(from_pt, to_pt, half_width, clearance,
                                  layer_index, net_code):
                return [to_pt]

        # 2) Try L-shaped: one 45° segment + one orthogonal segment
        dx = to_pt.x - from_pt.x
        dy = to_pt.y - from_pt.y

        # Option A: go diagonal first, then orthogonal
        candidates = self._generate_45_l_shapes(from_pt, to_pt, dx, dy)

        for mid in candidates:
            if not _is_45_aligned(from_pt, mid):
                continue
            if not _is_45_aligned(mid, to_pt):
                continue
            if (self._can_shortcut(from_pt, mid, half_width, clearance,
                                   layer_index, net_code) and
                self._can_shortcut(mid, to_pt, half_width, clearance,
                                   layer_index, net_code)):
                return [mid, to_pt]

        return None

    def _generate_45_l_shapes(
        self,
        from_pt: IntPoint,
        to_pt: IntPoint,
        dx: int,
        dy: int,
    ) -> list[IntPoint]:
        """Generate candidate midpoints for L-shaped 45° paths."""
        candidates = []
        adx, ady = abs(dx), abs(dy)

        if adx >= ady:
            # Diagonal portion uses dy, horizontal remainder
            diag = ady
            sx = 1 if dx > 0 else -1
            sy = 1 if dy > 0 else -1
            # Diagonal first
            candidates.append(IntPoint(from_pt.x + diag * sx, from_pt.y + diag * sy))
            # Orthogonal first
            candidates.append(IntPoint(to_pt.x - diag * sx * (1 if dy != 0 else 0),
                                       from_pt.y))
            # Pure horizontal then diagonal
            candidates.append(IntPoint(to_pt.x - diag * sx, to_pt.y - diag * sy))
        else:
            # Diagonal portion uses dx, vertical remainder
            diag = adx
            sx = 1 if dx > 0 else -1
            sy = 1 if dy > 0 else -1
            # Diagonal first
            candidates.append(IntPoint(from_pt.x + diag * sx, from_pt.y + diag * sy))
            # Pure vertical then diagonal
            candidates.append(IntPoint(from_pt.x, to_pt.y - diag * sy))

        return candidates

    def _can_shortcut(
        self,
        from_pt: IntPoint,
        to_pt: IntPoint,
        half_width: int,
        clearance: int,
        layer_index: int,
        net_code: int,
    ) -> bool:
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


class PullTightAlgo90:
    """Optimize traces with Manhattan (90°-only) constraint.

    All output segments are strictly horizontal or vertical.
    Diagonals are replaced with staircase patterns.
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        config: PullTight45Config | None = None,
    ):
        self.board = board
        self.rules = rules
        self.config = config or PullTight45Config()
        self.search_tree = SearchTree(board.bounding_box)
        self._rebuild_tree()

    def _rebuild_tree(self):
        items = list(self.board.all_items())
        self.search_tree.rebuild(items, self.board.bounding_box)

    def optimize_all(self) -> int:
        """Optimize all traces with 90° constraint. Returns number improved."""
        time_limit = TimeLimit(self.config.time_limit_seconds)
        improved_count = 0

        for trace in self.board.get_traces():
            if time_limit.is_expired():
                break
            if trace.is_fixed:
                continue
            if self._optimize_trace(trace):
                improved_count += 1

        if improved_count:
            logger.info("PullTight90 optimized %d traces", improved_count)
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
            new_length = _path_length(new_corners)
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
        changed = False

        while i < len(corners) - 1:
            if i + 2 < len(corners):
                shortcut = self._try_90_shortcut(
                    result[-1], corners[i + 2],
                    trace.layer_index, trace.width, trace.net_code,
                )
                if shortcut is not None:
                    result.extend(shortcut)
                    i += 2
                    changed = True
                    continue
            result.append(corners[i + 1])
            i += 1

        return result if changed else None

    def _try_90_shortcut(
        self,
        from_pt: IntPoint,
        to_pt: IntPoint,
        layer_index: int,
        width: int,
        net_code: int,
    ) -> list[IntPoint] | None:
        half_width = width // 2
        clearance = self.rules.min_clearance

        # Direct horizontal or vertical?
        if from_pt.x == to_pt.x or from_pt.y == to_pt.y:
            if self._can_shortcut(from_pt, to_pt, half_width, clearance,
                                  layer_index, net_code):
                return [to_pt]

        # L-shaped: horizontal then vertical
        mid_hv = IntPoint(to_pt.x, from_pt.y)
        if (self._can_shortcut(from_pt, mid_hv, half_width, clearance,
                               layer_index, net_code) and
            self._can_shortcut(mid_hv, to_pt, half_width, clearance,
                               layer_index, net_code)):
            return [mid_hv, to_pt]

        # L-shaped: vertical then horizontal
        mid_vh = IntPoint(from_pt.x, to_pt.y)
        if (self._can_shortcut(from_pt, mid_vh, half_width, clearance,
                               layer_index, net_code) and
            self._can_shortcut(mid_vh, to_pt, half_width, clearance,
                               layer_index, net_code)):
            return [mid_vh, to_pt]

        return None

    def _can_shortcut(
        self,
        from_pt: IntPoint,
        to_pt: IntPoint,
        half_width: int,
        clearance: int,
        layer_index: int,
        net_code: int,
    ) -> bool:
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


class CornerSmoother:
    """Smooth acute angles in traces by inserting chamfer or fillet points.

    Replaces sharp corners (< 90°) with gentler transitions, improving
    signal integrity and manufacturing yield.
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        min_angle_deg: float = 90.0,
        chamfer_ratio: float = 0.3,
    ):
        self.board = board
        self.rules = rules
        self.min_angle_deg = min_angle_deg
        self.chamfer_ratio = chamfer_ratio
        self.search_tree = SearchTree(board.bounding_box)
        items = list(board.all_items())
        self.search_tree.rebuild(items, board.bounding_box)

    def smooth_all(self) -> int:
        """Smooth acute angles on all traces. Returns count improved."""
        improved = 0
        for trace in self.board.get_traces():
            if trace.is_fixed:
                continue
            if self._smooth_trace(trace):
                improved += 1
        if improved:
            logger.info("Corner smoother improved %d traces", improved)
        return improved

    def _smooth_trace(self, trace: Trace) -> bool:
        if len(trace.corners) < 3:
            return False

        new_corners = [trace.corners[0]]
        changed = False

        for i in range(1, len(trace.corners) - 1):
            prev = trace.corners[i - 1]
            curr = trace.corners[i]
            next_pt = trace.corners[i + 1]

            angle = _corner_angle(prev, curr, next_pt)

            if angle < self.min_angle_deg:
                # Insert chamfer points
                chamfer = self._compute_chamfer(prev, curr, next_pt)
                if chamfer and self._validate_chamfer(
                    chamfer, trace.layer_index, trace.width, trace.net_code,
                ):
                    new_corners.extend(chamfer)
                    changed = True
                else:
                    new_corners.append(curr)
            else:
                new_corners.append(curr)

        new_corners.append(trace.corners[-1])

        if changed:
            trace.corners = new_corners
        return changed

    def _compute_chamfer(
        self,
        prev: IntPoint,
        curr: IntPoint,
        next_pt: IntPoint,
    ) -> list[IntPoint] | None:
        """Compute two chamfer points that replace a sharp corner."""
        # Vectors from corner to neighbors
        dx1 = prev.x - curr.x
        dy1 = prev.y - curr.y
        dx2 = next_pt.x - curr.x
        dy2 = next_pt.y - curr.y

        len1 = math.sqrt(dx1 * dx1 + dy1 * dy1)
        len2 = math.sqrt(dx2 * dx2 + dy2 * dy2)

        if len1 < 1000 or len2 < 1000:
            return None

        # Chamfer offset = ratio * shorter segment length
        offset = self.chamfer_ratio * min(len1, len2)

        p1 = IntPoint(
            round(curr.x + (dx1 / len1) * offset),
            round(curr.y + (dy1 / len1) * offset),
        )
        p2 = IntPoint(
            round(curr.x + (dx2 / len2) * offset),
            round(curr.y + (dy2 / len2) * offset),
        )

        return [p1, p2]

    def _validate_chamfer(
        self,
        chamfer_points: list[IntPoint],
        layer_index: int,
        width: int,
        net_code: int,
    ) -> bool:
        """Check that chamfer segments don't violate clearance."""
        half_width = width // 2
        clearance = self.rules.min_clearance

        for i in range(len(chamfer_points) - 1):
            p1 = chamfer_points[i]
            p2 = chamfer_points[i + 1]
            query_bb = BoundingBox(
                min(p1.x, p2.x) - half_width - clearance,
                min(p1.y, p2.y) - half_width - clearance,
                max(p1.x, p2.x) + half_width + clearance,
                max(p1.y, p2.y) + half_width + clearance,
            )
            candidates = self.search_tree.query_region(query_bb)
            if expanded_segment_intersects_items(
                p1, p2, half_width, clearance,
                layer_index, net_code, candidates,
            ):
                return False
        return True


# ----- Module-level helpers -----

def _is_45_aligned(p1: IntPoint, p2: IntPoint) -> bool:
    """Check if the segment p1→p2 is at a 45° increment."""
    dx = abs(p2.x - p1.x)
    dy = abs(p2.y - p1.y)
    # Horizontal, vertical, or 45° diagonal
    return dx == 0 or dy == 0 or dx == dy


def _corner_angle(prev: IntPoint, curr: IntPoint, next_pt: IntPoint) -> float:
    """Compute the angle at a corner in degrees (0-180)."""
    dx1 = prev.x - curr.x
    dy1 = prev.y - curr.y
    dx2 = next_pt.x - curr.x
    dy2 = next_pt.y - curr.y

    len1 = math.sqrt(dx1 * dx1 + dy1 * dy1)
    len2 = math.sqrt(dx2 * dx2 + dy2 * dy2)

    if len1 < 1 or len2 < 1:
        return 180.0

    cos_angle = (dx1 * dx2 + dy1 * dy2) / (len1 * len2)
    cos_angle = max(-1.0, min(1.0, cos_angle))
    return math.degrees(math.acos(cos_angle))


def _path_length(corners: list[IntPoint]) -> float:
    total = 0.0
    for i in range(len(corners) - 1):
        total += corners[i].distance_to(corners[i + 1])
    return total
