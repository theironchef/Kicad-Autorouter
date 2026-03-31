"""
FanoutAlgo — Automatic pad escape routing for dense packages.

BGA and QFP packages have tightly-spaced pads that need short "escape"
traces routed outward before the main autorouter can connect them. The
fanout algorithm routes these escape traces in optimal directions,
placing vias just outside the package boundary to transition to inner
routing layers.

Fanout strategy:
1. Identify component pads that need escape routing
2. Determine optimal escape direction per pad (outward from package center)
3. Route short escape trace in that direction
4. Place via at escape point for layer transition (if multi-layer)
5. Mark the escape endpoint as the new routing target for the maze router

This replicates Freerouting's FanoutAlgo concept.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum, auto

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.geometry.collision import expanded_segment_intersects_items
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.component import Component
from kicad_autorouter.board.pad import Pad
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.datastructures.search_tree import SearchTree
from kicad_autorouter.utils.timing import TimeLimit

logger = logging.getLogger(__name__)


class FanoutDirection(Enum):
    """Direction strategy for pad escape routing."""
    OUTWARD = auto()      # Radially outward from component center
    NEAREST_EDGE = auto() # Toward the nearest component boundary edge
    PREFERRED = auto()    # Follow layer preferred direction


@dataclass
class FanoutConfig:
    """Configuration for fanout routing."""
    escape_length: int = 1_000_000   # 1mm default escape trace length
    min_escape_length: int = 500_000  # 0.5mm minimum
    max_escape_length: int = 3_000_000  # 3mm maximum
    direction: FanoutDirection = FanoutDirection.OUTWARD
    place_vias: bool = True           # Place vias at escape endpoints
    max_passes: int = 3               # Max fanout attempts per component
    time_limit_seconds: float = 30.0
    diff_pair_first: bool = True      # Fan out differential pairs first


@dataclass
class FanoutResult:
    """Result of fanout routing."""
    pads_fanned: int = 0
    vias_placed: int = 0
    components_processed: int = 0
    failed_pads: int = 0
    diff_pairs_fanned: int = 0


class FanoutAlgo:
    """Route escape traces for dense package pads.

    For each component with tightly-spaced pads, routes short escape
    traces outward from the package center and optionally places vias
    at the escape endpoints for layer transition.
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        config: FanoutConfig | None = None,
    ):
        self.board = board
        self.rules = rules
        self.config = config or FanoutConfig()
        self.search_tree = SearchTree(board.bounding_box)
        self._rebuild_tree()

    def _rebuild_tree(self):
        items = list(self.board.all_items())
        self.search_tree.rebuild(items, self.board.bounding_box)

    def fanout_all(self) -> FanoutResult:
        """Run fanout on all components that need it."""
        time_limit = TimeLimit(self.config.time_limit_seconds)
        result = FanoutResult()

        # Find components with dense pads that need fanout
        components = self._find_fanout_candidates()

        # Handle differential pairs first if enabled
        if self.config.diff_pair_first:
            diff_pairs = self._detect_diff_pairs()
            for net_p, net_n in diff_pairs:
                if time_limit.is_expired():
                    break
                dp_result = self._fanout_diff_pair(net_p, net_n)
                result.pads_fanned += dp_result.pads_fanned
                result.vias_placed += dp_result.vias_placed
                result.diff_pairs_fanned += 1
                # Rebuild tree after differential pair fanout
                self._rebuild_tree()

        for comp in components:
            if time_limit.is_expired():
                break
            comp_result = self._fanout_component(comp)
            result.pads_fanned += comp_result.pads_fanned
            result.vias_placed += comp_result.vias_placed
            result.failed_pads += comp_result.failed_pads
            result.components_processed += 1

        if result.pads_fanned:
            logger.info(
                "Fanout: %d pads escaped, %d vias placed (%d diff pairs) across %d components",
                result.pads_fanned, result.vias_placed, result.diff_pairs_fanned,
                result.components_processed,
            )

        return result

    def fanout_component(self, component_id: int) -> FanoutResult:
        """Run fanout on a specific component."""
        comp = self.board.components.get(component_id)
        if comp is None:
            return FanoutResult()
        return self._fanout_component(comp)

    def _find_fanout_candidates(self) -> list[Component]:
        """Find components that would benefit from fanout routing.

        Criteria: components with >= 4 pads in a dense arrangement.
        """
        candidates = []
        for comp in self.board.components.values():
            pads = [p for p in self.board.get_pads()
                    if p.component_id == comp.id]
            if len(pads) < 4:
                continue

            # Check density: average pad spacing < 2 * trace_width
            if len(pads) >= 2:
                avg_spacing = self._average_pad_spacing(pads)
                threshold = self.rules.min_trace_width * 4
                if avg_spacing < threshold:
                    candidates.append(comp)

        return candidates

    def _fanout_component(self, comp: Component) -> FanoutResult:
        """Route escape traces for all pads in a component."""
        result = FanoutResult()

        pads = [p for p in self.board.get_pads()
                if p.component_id == comp.id]
        if not pads:
            return result

        # Compute component center for outward direction
        center = self._component_center(pads)

        # Sort pads: outermost first (they're easiest to escape)
        pads.sort(key=lambda p: -p.position.distance_squared(center))

        net_class = self.board.default_net_class

        for pad in pads:
            if pad.net_code <= 0:
                continue

            # Skip pads that already have traces connected
            existing = self.board.get_traces_on_net(pad.net_code)
            if existing:
                pad_connected = False
                for t in existing:
                    if t.first_corner and t.first_corner.distance_squared(pad.position) <= pad.size_x ** 2:
                        pad_connected = True
                        break
                    if t.last_corner and t.last_corner.distance_squared(pad.position) <= pad.size_x ** 2:
                        pad_connected = True
                        break
                if pad_connected:
                    continue

            escape_pt = self._compute_escape_point(pad, center)
            if escape_pt is None:
                result.failed_pads += 1
                continue

            # Validate escape trace
            trace_width = self.rules.get_trace_width(net_class)
            half_width = trace_width // 2
            clearance = self.rules.get_clearance(net_class)

            query_bb = BoundingBox(
                min(pad.position.x, escape_pt.x) - half_width - clearance,
                min(pad.position.y, escape_pt.y) - half_width - clearance,
                max(pad.position.x, escape_pt.x) + half_width + clearance,
                max(pad.position.y, escape_pt.y) + half_width + clearance,
            )
            candidates = self.search_tree.query_region(query_bb)

            if expanded_segment_intersects_items(
                pad.position, escape_pt,
                half_width, clearance,
                pad.layer_indices[0] if pad.layer_indices else 0,
                pad.net_code, candidates,
            ):
                result.failed_pads += 1
                continue

            # Place the escape trace
            layer = pad.layer_indices[0] if pad.layer_indices else 0
            self.board.add_trace(
                corners=[pad.position, escape_pt],
                width=trace_width,
                layer_index=layer,
                net_code=pad.net_code,
            )
            result.pads_fanned += 1

            # Place via at escape point if multi-layer
            if self.config.place_vias and self.board.layer_structure.copper_layer_count >= 2:
                total_layers = self.board.layer_structure.copper_layer_count
                v_diam, v_drill, _ = self.rules.select_via_type(
                    0, total_layers - 1, total_layers, net_class,
                )
                self.board.add_via(
                    position=escape_pt,
                    diameter=v_diam,
                    drill=v_drill,
                    start_layer=0,
                    end_layer=total_layers - 1,
                    net_code=pad.net_code,
                )
                result.vias_placed += 1

            # Rebuild tree after modification
            self._rebuild_tree()

        return result

    def _detect_diff_pairs(self) -> list[tuple[int, int]]:
        """Detect differential pairs by net naming convention.

        Matches nets ending with _P/_N, +/-, or P/N suffixes.
        Returns list of (net_code_p, net_code_n) tuples.
        """
        diff_pairs = []
        nets_by_name = {}

        # Build map of net names to their codes
        for net_code, net in self.board.nets.items():
            if net_code <= 0:
                continue
            nets_by_name[net.name] = net_code

        # Find pairs by naming convention
        matched_pairs = set()
        for name, code_p in nets_by_name.items():
            if code_p in matched_pairs:
                continue

            # Check for _P/_N suffix
            for suffix_p, suffix_n in [("_P", "_N"), ("+", "-"), ("P", "N")]:
                if name.endswith(suffix_p):
                    base = name[:-len(suffix_p)]
                    name_n = base + suffix_n
                    if name_n in nets_by_name:
                        code_n = nets_by_name[name_n]
                        if code_n not in matched_pairs:
                            diff_pairs.append((code_p, code_n))
                            matched_pairs.add(code_p)
                            matched_pairs.add(code_n)
                            break

        return diff_pairs

    def _fanout_diff_pair(self, net_code_p: int, net_code_n: int) -> FanoutResult:
        """Fan out a differential pair together on the same layer and side.

        Both nets escape on the same layer, adjacent to each other,
        maintaining the differential pair gap from the net class.
        """
        result = FanoutResult()

        # Get pads for both nets
        pad_p = None
        pad_n = None
        for pad in self.board.get_pads():
            if pad.net_code == net_code_p:
                pad_p = pad
            elif pad.net_code == net_code_n:
                pad_n = pad

        if pad_p is None or pad_n is None:
            return result

        net_class = self.board.default_net_class

        # Compute component center from both pads
        center = self._component_center([pad_p, pad_n])

        # Compute escape points for both pads
        escape_p = self._compute_escape_point(pad_p, center)
        escape_n = self._compute_escape_point(pad_n, center)

        if escape_p is None or escape_n is None:
            result.failed_pads += 2
            return result

        # Adjust escape points to be adjacent with differential pair gap
        trace_width = self.rules.get_trace_width(net_class)
        diff_pair_gap = self.rules.get_differential_pair_gap(net_class)

        # Move escape points to be side-by-side with proper spacing
        # This is a simplified approach: align them perpendicular to escape direction
        dx = escape_p.x - pad_p.position.x
        dy = escape_p.y - pad_p.position.y
        dist = math.sqrt(dx * dx + dy * dy) if dx or dy else 1.0

        if dist > 1.0:
            nx = dx / dist
            ny = dy / dist
            # Perpendicular direction for spacing
            px = -ny
            py = nx

            offset = trace_width // 2 + diff_pair_gap // 2
            escape_p = IntPoint(
                int(escape_p.x + px * offset),
                int(escape_p.y + py * offset),
            )
            escape_n = IntPoint(
                int(escape_n.x - px * offset),
                int(escape_n.y - py * offset),
            )

        # Validate and place escape traces for both nets
        clearance = self.rules.get_clearance(net_class)
        layer = pad_p.layer_indices[0] if pad_p.layer_indices else 0

        for pad, escape_pt, net_code in [(pad_p, escape_p, net_code_p),
                                          (pad_n, escape_n, net_code_n)]:
            query_bb = BoundingBox(
                min(pad.position.x, escape_pt.x) - trace_width - clearance,
                min(pad.position.y, escape_pt.y) - trace_width - clearance,
                max(pad.position.x, escape_pt.x) + trace_width + clearance,
                max(pad.position.y, escape_pt.y) + trace_width + clearance,
            )
            candidates = self.search_tree.query_region(query_bb)

            if expanded_segment_intersects_items(
                pad.position, escape_pt,
                trace_width // 2, clearance,
                layer, net_code, candidates,
            ):
                result.failed_pads += 1
                continue

            # Place escape trace
            self.board.add_trace(
                corners=[pad.position, escape_pt],
                width=trace_width,
                layer_index=layer,
                net_code=net_code,
            )
            result.pads_fanned += 1

            # Place via if multi-layer
            if self.config.place_vias and self.board.layer_structure.copper_layer_count >= 2:
                total_layers = self.board.layer_structure.copper_layer_count
                v_diam, v_drill, _ = self.rules.select_via_type(
                    0, total_layers - 1, total_layers, net_class,
                )
                self.board.add_via(
                    position=escape_pt,
                    diameter=v_diam,
                    drill=v_drill,
                    start_layer=0,
                    end_layer=total_layers - 1,
                    net_code=net_code,
                )
                result.vias_placed += 1

        return result

    def _compute_escape_point(
        self,
        pad: Pad,
        component_center: IntPoint,
    ) -> IntPoint | None:
        """Compute the escape point for a pad."""
        dx = pad.position.x - component_center.x
        dy = pad.position.y - component_center.y
        dist = math.sqrt(dx * dx + dy * dy)

        if dist < 100:
            # Pad is at component center — escape upward
            dx, dy = 0, -1
            dist = 1.0

        # Normalize direction
        nx = dx / dist
        ny = dy / dist

        # Snap to nearest 45° direction for cleaner routing
        angle = math.atan2(ny, nx)
        snapped = round(angle / (math.pi / 4)) * (math.pi / 4)
        nx = math.cos(snapped)
        ny = math.sin(snapped)

        # Try escape lengths from preferred to minimum
        for length in range(self.config.escape_length,
                           self.config.min_escape_length - 1,
                           -100_000):  # Step by 100μm
            escape_pt = IntPoint(
                round(pad.position.x + nx * length),
                round(pad.position.y + ny * length),
            )

            # Check board bounds
            bb = self.board.bounding_box
            if (bb.x_min <= escape_pt.x <= bb.x_max and
                    bb.y_min <= escape_pt.y <= bb.y_max):
                return escape_pt

        return None

    @staticmethod
    def _component_center(pads: list[Pad]) -> IntPoint:
        """Compute center of mass of component pads."""
        if not pads:
            return IntPoint(0, 0)
        sx = sum(p.position.x for p in pads)
        sy = sum(p.position.y for p in pads)
        return IntPoint(sx // len(pads), sy // len(pads))

    @staticmethod
    def _average_pad_spacing(pads: list[Pad]) -> float:
        """Compute average nearest-neighbor distance between pads."""
        if len(pads) < 2:
            return float('inf')

        total_nearest = 0.0
        for i, p in enumerate(pads):
            nearest = float('inf')
            for j, q in enumerate(pads):
                if i == j:
                    continue
                d = math.sqrt(p.position.distance_squared(q.position))
                if d < nearest:
                    nearest = d
            total_nearest += nearest

        return total_nearest / len(pads)
