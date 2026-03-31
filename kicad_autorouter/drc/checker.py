"""
Design Rule Checker — clearance, connectivity, and via validation.

The DrcChecker runs all sub-checks against a RoutingBoard and returns
a DrcResult with every violation found.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field

from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.item import Item
from kicad_autorouter.board.pad import Pad
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.via import Via
from kicad_autorouter.board.obstacle import ObstacleArea
from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.collision import (
    segment_clearance_to_segment,
    segment_clearance_to_octagon,
    _point_to_segment_dist,
)
from kicad_autorouter.geometry.octagon import IntOctagon
from kicad_autorouter.drc.violations import (
    DrcResult,
    DrcViolation,
    ViolationType,
    Severity,
)

logger = logging.getLogger(__name__)


@dataclass
class DrcConfig:
    """Controls which DRC checks to run."""

    check_clearances: bool = True
    check_hole_clearance: bool = True
    check_connectivity: bool = True
    check_dangles: bool = True
    check_single_layer_vias: bool = True
    check_board_edge: bool = True
    deduplicate: bool = True


class DrcChecker:
    """Runs all design-rule checks against a board.

    Usage::

        checker = DrcChecker(board)
        result = checker.run()
        if result.has_errors:
            for v in result.violations:
                print(v)
    """

    def __init__(self, board: RoutingBoard, config: DrcConfig | None = None):
        self.board = board
        self.config = config or DrcConfig()

    def run(self) -> DrcResult:
        """Execute all enabled checks and return aggregated results."""
        t0 = time.monotonic()
        violations: list[DrcViolation] = []

        if self.config.check_clearances:
            violations.extend(self._check_clearances())

        if self.config.check_hole_clearance:
            violations.extend(self._check_hole_clearances())

        if self.config.check_connectivity:
            violations.extend(self._check_connectivity())

        if self.config.check_dangles:
            violations.extend(self._check_dangles())

        if self.config.check_single_layer_vias:
            violations.extend(self._check_single_layer_vias())

        if self.config.check_board_edge:
            violations.extend(self._check_board_edge_clearance())

        elapsed = (time.monotonic() - t0) * 1000.0

        result = DrcResult(
            violations=violations,
            board_items_checked=self.board.item_count,
            nets_checked=len(self.board.nets),
            elapsed_ms=elapsed,
        )

        if self.config.deduplicate:
            result = result.deduplicate()

        return result

    # ------------------------------------------------------------------
    # Clearance checks
    # ------------------------------------------------------------------

    def _check_clearances(self) -> list[DrcViolation]:
        """Check copper-to-copper clearances on each layer."""
        violations: list[DrcViolation] = []
        traces = self.board.get_traces()
        vias = self.board.get_vias()
        pads = self.board.get_pads()
        obstacles = self.board.get_obstacles()

        # Trace-to-trace
        for i, t1 in enumerate(traces):
            for t2 in traces[i + 1:]:
                v = self._check_trace_trace(t1, t2)
                if v:
                    violations.append(v)

        # Trace-to-pad
        for trace in traces:
            for pad in pads:
                v = self._check_trace_pad(trace, pad)
                if v:
                    violations.append(v)

        # Trace-to-via
        for trace in traces:
            for via in vias:
                v = self._check_trace_via(trace, via)
                if v:
                    violations.append(v)

        # Via-to-via
        for i, v1 in enumerate(vias):
            for v2 in vias[i + 1:]:
                v = self._check_via_via(v1, v2)
                if v:
                    violations.append(v)

        # Via-to-pad
        for via in vias:
            for pad in pads:
                v = self._check_via_pad(via, pad)
                if v:
                    violations.append(v)

        # Pad-to-pad
        for i, p1 in enumerate(pads):
            for p2 in pads[i + 1:]:
                v = self._check_pad_pad(p1, p2)
                if v:
                    violations.append(v)

        # Trace-to-obstacle / Via-to-obstacle
        for trace in traces:
            for obs in obstacles:
                v = self._check_trace_obstacle(trace, obs)
                if v:
                    violations.append(v)
        for via in vias:
            for obs in obstacles:
                v = self._check_via_obstacle(via, obs)
                if v:
                    violations.append(v)

        return violations

    def _required_clearance(self, item_a: Item, item_b: Item) -> int:
        """Get the required clearance between two items."""
        rules = self.board.design_rules
        nc_a = self.board.get_net_class_for_net(item_a.net_code)
        nc_b = self.board.get_net_class_for_net(item_b.net_code)
        return max(rules.get_clearance(nc_a), rules.get_clearance(nc_b))

    def _shares_net(self, a: Item, b: Item) -> bool:
        return a.shares_net(b)

    def _shared_layers(self, a: Item, b: Item) -> list[int]:
        return [l for l in a.layer_indices if l in b.layer_indices]

    def _check_trace_trace(self, t1: Trace, t2: Trace) -> DrcViolation | None:
        if self._shares_net(t1, t2):
            return None
        if t1.layer_index != t2.layer_index:
            return None
        required = self._required_clearance(t1, t2)
        min_dist = self._trace_to_trace_distance(t1, t2)
        # Subtract half-widths to get copper-to-copper distance
        copper_dist = min_dist - t1.half_width - t2.half_width
        if copper_dist < required:
            midpoint = self._trace_midpoint(t1)
            return DrcViolation(
                violation_type=ViolationType.TRACE_TRACE_CLEARANCE,
                severity=Severity.ERROR,
                message=f"Trace-trace clearance {copper_dist / 1e6:.3f}mm < {required / 1e6:.3f}mm",
                location=midpoint,
                layer_index=t1.layer_index,
                item_ids=(t1.id, t2.id),
                net_codes=(t1.net_code, t2.net_code),
                actual_value=copper_dist,
                required_value=required,
            )
        return None

    def _check_trace_pad(self, trace: Trace, pad: Pad) -> DrcViolation | None:
        if self._shares_net(trace, pad):
            return None
        if trace.layer_index not in pad.layer_indices:
            return None
        required = self._required_clearance(trace, pad)
        pad_shape = pad.get_shape_on_layer(trace.layer_index)
        if pad_shape is None:
            return None
        min_dist = self._trace_to_shape_distance(trace, pad_shape)
        copper_dist = min_dist - trace.half_width
        if copper_dist < required:
            return DrcViolation(
                violation_type=ViolationType.TRACE_PAD_CLEARANCE,
                severity=Severity.ERROR,
                message=f"Trace-pad clearance {copper_dist / 1e6:.3f}mm < {required / 1e6:.3f}mm",
                location=pad.position,
                layer_index=trace.layer_index,
                item_ids=(trace.id, pad.id),
                net_codes=(trace.net_code, pad.net_code),
                actual_value=copper_dist,
                required_value=required,
            )
        return None

    def _check_trace_via(self, trace: Trace, via: Via) -> DrcViolation | None:
        if self._shares_net(trace, via):
            return None
        if trace.layer_index not in via.layer_indices:
            return None
        required = self._required_clearance(trace, via)
        min_dist = self._trace_to_point_distance(trace, via.position)
        copper_dist = min_dist - trace.half_width - via.radius
        if copper_dist < required:
            return DrcViolation(
                violation_type=ViolationType.TRACE_VIA_CLEARANCE,
                severity=Severity.ERROR,
                message=f"Trace-via clearance {copper_dist / 1e6:.3f}mm < {required / 1e6:.3f}mm",
                location=via.position,
                layer_index=trace.layer_index,
                item_ids=(trace.id, via.id),
                net_codes=(trace.net_code, via.net_code),
                actual_value=copper_dist,
                required_value=required,
            )
        return None

    def _check_via_via(self, v1: Via, v2: Via) -> DrcViolation | None:
        if self._shares_net(v1, v2):
            return None
        shared = self._shared_layers(v1, v2)
        if not shared:
            return None
        required = self._required_clearance(v1, v2)
        dist = math.sqrt(v1.position.distance_squared(v2.position))
        copper_dist = dist - v1.radius - v2.radius
        if copper_dist < required:
            mid = IntPoint((v1.position.x + v2.position.x) // 2,
                           (v1.position.y + v2.position.y) // 2)
            return DrcViolation(
                violation_type=ViolationType.VIA_VIA_CLEARANCE,
                severity=Severity.ERROR,
                message=f"Via-via clearance {copper_dist / 1e6:.3f}mm < {required / 1e6:.3f}mm",
                location=mid,
                layer_index=shared[0],
                item_ids=(v1.id, v2.id),
                net_codes=(v1.net_code, v2.net_code),
                actual_value=copper_dist,
                required_value=required,
            )
        return None

    def _check_via_pad(self, via: Via, pad: Pad) -> DrcViolation | None:
        if self._shares_net(via, pad):
            return None
        shared = self._shared_layers(via, pad)
        if not shared:
            return None
        required = self._required_clearance(via, pad)
        dist = math.sqrt(via.position.distance_squared(pad.position))
        pad_half = max(pad.size_x, pad.size_y) // 2
        copper_dist = dist - via.radius - pad_half
        if copper_dist < required:
            return DrcViolation(
                violation_type=ViolationType.VIA_PAD_CLEARANCE,
                severity=Severity.ERROR,
                message=f"Via-pad clearance {copper_dist / 1e6:.3f}mm < {required / 1e6:.3f}mm",
                location=via.position,
                layer_index=shared[0],
                item_ids=(via.id, pad.id),
                net_codes=(via.net_code, pad.net_code),
                actual_value=copper_dist,
                required_value=required,
            )
        return None

    def _check_pad_pad(self, p1: Pad, p2: Pad) -> DrcViolation | None:
        if self._shares_net(p1, p2):
            return None
        shared = self._shared_layers(p1, p2)
        if not shared:
            return None
        required = self._required_clearance(p1, p2)
        dist = math.sqrt(p1.position.distance_squared(p2.position))
        half1 = max(p1.size_x, p1.size_y) // 2
        half2 = max(p2.size_x, p2.size_y) // 2
        copper_dist = dist - half1 - half2
        if copper_dist < required:
            mid = IntPoint((p1.position.x + p2.position.x) // 2,
                           (p1.position.y + p2.position.y) // 2)
            return DrcViolation(
                violation_type=ViolationType.PAD_PAD_CLEARANCE,
                severity=Severity.ERROR,
                message=f"Pad-pad clearance {copper_dist / 1e6:.3f}mm < {required / 1e6:.3f}mm",
                location=mid,
                layer_index=shared[0],
                item_ids=(p1.id, p2.id),
                net_codes=(p1.net_code, p2.net_code),
                actual_value=copper_dist,
                required_value=required,
            )
        return None

    def _check_trace_obstacle(self, trace: Trace, obs: ObstacleArea) -> DrcViolation | None:
        shared = self._shared_layers(trace, obs)
        if not shared:
            return None
        required = self._required_clearance(trace, obs)
        obs_shape = obs.get_shape_on_layer(trace.layer_index)
        if obs_shape is None:
            return None
        min_dist = self._trace_to_shape_distance(trace, obs_shape)
        copper_dist = min_dist - trace.half_width
        if copper_dist < required:
            return DrcViolation(
                violation_type=ViolationType.TRACE_OBSTACLE_CLEARANCE,
                severity=Severity.ERROR,
                message=f"Trace-obstacle clearance {copper_dist / 1e6:.3f}mm < {required / 1e6:.3f}mm",
                location=self._trace_midpoint(trace),
                layer_index=trace.layer_index,
                item_ids=(trace.id, obs.id),
                net_codes=(trace.net_code,),
                actual_value=copper_dist,
                required_value=required,
            )
        return None

    def _check_via_obstacle(self, via: Via, obs: ObstacleArea) -> DrcViolation | None:
        shared = self._shared_layers(via, obs)
        if not shared:
            return None
        layer = shared[0]
        required = self._required_clearance(via, obs)
        obs_shape = obs.get_shape_on_layer(layer)
        if obs_shape is None:
            return None
        if isinstance(obs_shape, IntOctagon):
            # Distance from via center to octagon, minus via radius
            verts = obs_shape._vertices()
            min_d = float('inf')
            for i in range(len(verts)):
                j = (i + 1) % len(verts)
                d = _point_to_segment_dist(via.position, verts[i], verts[j])
                if d < min_d:
                    min_d = d
            # If via center is inside octagon, distance is 0
            if obs_shape.contains(via.position):
                min_d = 0.0
            copper_dist = min_d - via.radius
        else:
            copper_dist = float('inf')
        if copper_dist < required:
            return DrcViolation(
                violation_type=ViolationType.VIA_OBSTACLE_CLEARANCE,
                severity=Severity.ERROR,
                message=f"Via-obstacle clearance {copper_dist / 1e6:.3f}mm < {required / 1e6:.3f}mm",
                location=via.position,
                layer_index=layer,
                item_ids=(via.id, obs.id),
                net_codes=(via.net_code,),
                actual_value=copper_dist,
                required_value=required,
            )
        return None

    # ------------------------------------------------------------------
    # Hole clearance
    # ------------------------------------------------------------------

    def _check_hole_clearances(self) -> list[DrcViolation]:
        """Check drill hole to copper clearances."""
        violations: list[DrcViolation] = []
        vias = self.board.get_vias()
        pads = [p for p in self.board.get_pads() if p.is_through_hole]
        traces = self.board.get_traces()

        # All drill items: vias + through-hole pads
        drill_items: list[tuple[IntPoint, int, int, Item]] = []  # position, drill_radius, net, item
        for via in vias:
            drill_items.append((via.position, via.drill_radius, via.net_code, via))
        for pad in pads:
            drill_items.append((pad.position, pad.drill_diameter // 2, pad.net_code, pad))

        hole_clearance = self.board.design_rules.min_clearance

        # Check drill to trace
        for pos, drill_r, net, drill_item in drill_items:
            for trace in traces:
                if net == trace.net_code:
                    continue
                # Check if trace passes through any layer
                min_dist = self._trace_to_point_distance(trace, pos)
                copper_dist = min_dist - trace.half_width - drill_r
                if copper_dist < hole_clearance:
                    violations.append(DrcViolation(
                        violation_type=ViolationType.HOLE_CLEARANCE,
                        severity=Severity.ERROR,
                        message=f"Hole-trace clearance {copper_dist / 1e6:.3f}mm < {hole_clearance / 1e6:.3f}mm",
                        location=pos,
                        layer_index=-1,
                        item_ids=(drill_item.id, trace.id),
                        net_codes=(net, trace.net_code),
                        actual_value=copper_dist,
                        required_value=hole_clearance,
                    ))

        # Check drill to drill
        for i, (pos1, r1, net1, item1) in enumerate(drill_items):
            for pos2, r2, net2, item2 in drill_items[i + 1:]:
                if net1 == net2:
                    continue
                dist = math.sqrt(pos1.distance_squared(pos2))
                copper_dist = dist - r1 - r2
                if copper_dist < hole_clearance:
                    violations.append(DrcViolation(
                        violation_type=ViolationType.HOLE_CLEARANCE,
                        severity=Severity.ERROR,
                        message=f"Hole-hole clearance {copper_dist / 1e6:.3f}mm < {hole_clearance / 1e6:.3f}mm",
                        location=pos1,
                        layer_index=-1,
                        item_ids=(item1.id, item2.id),
                        net_codes=(net1, net2),
                        actual_value=copper_dist,
                        required_value=hole_clearance,
                    ))

        return violations

    # ------------------------------------------------------------------
    # Connectivity checks
    # ------------------------------------------------------------------

    def _check_connectivity(self) -> list[DrcViolation]:
        """Check for unconnected nets and disconnected net groups."""
        violations: list[DrcViolation] = []

        for net_code, net in self.board.nets.items():
            if net_code <= 0:
                continue
            pads = self.board.get_pads_on_net(net_code)
            if len(pads) < 2:
                continue

            unconnected = self.board.get_unconnected_pad_pairs(net_code)
            if unconnected:
                # Report each unconnected pair
                for p1, p2 in unconnected:
                    mid = IntPoint((p1.position.x + p2.position.x) // 2,
                                   (p1.position.y + p2.position.y) // 2)
                    dist = math.sqrt(p1.position.distance_squared(p2.position))
                    violations.append(DrcViolation(
                        violation_type=ViolationType.UNCONNECTED_ITEMS,
                        severity=Severity.ERROR,
                        message=f"Unconnected pads on net '{net.name}' (distance {dist / 1e6:.2f}mm)",
                        location=mid,
                        layer_index=-1,
                        item_ids=(p1.id, p2.id),
                        net_codes=(net_code,),
                        actual_value=dist,
                        required_value=0.0,
                    ))

                # Also report as disconnected net group if >1 pair
                if len(unconnected) > 1:
                    violations.append(DrcViolation(
                        violation_type=ViolationType.DISCONNECTED_NET_GROUP,
                        severity=Severity.WARNING,
                        message=f"Net '{net.name}' has {len(unconnected) + 1} disconnected groups",
                        location=pads[0].position,
                        layer_index=-1,
                        item_ids=tuple(p.id for p in pads),
                        net_codes=(net_code,),
                    ))

        return violations

    # ------------------------------------------------------------------
    # Dangling trace detection
    # ------------------------------------------------------------------

    def _check_dangles(self) -> list[DrcViolation]:
        """Find dangling trace endpoints not connected to anything."""
        violations: list[DrcViolation] = []
        tails = self.board.find_tails()
        for trace, tail_points in tails:
            for pt in tail_points:
                violations.append(DrcViolation(
                    violation_type=ViolationType.DANGLING_TRACE,
                    severity=Severity.WARNING,
                    message=f"Dangling trace endpoint on net {trace.net_code}",
                    location=pt,
                    layer_index=trace.layer_index,
                    item_ids=(trace.id,),
                    net_codes=(trace.net_code,),
                ))
        return violations

    # ------------------------------------------------------------------
    # Single-layer via detection
    # ------------------------------------------------------------------

    def _check_single_layer_vias(self) -> list[DrcViolation]:
        """Find vias that only connect one layer (useless)."""
        violations: list[DrcViolation] = []
        for via in self.board.get_vias():
            # A via that only has traces on one of its layers is useless
            net = via.net_code
            traces = self.board.get_traces_on_net(net)
            connected_layers: set[int] = set()
            for trace in traces:
                if trace.layer_index in via.layer_indices:
                    # Check if trace actually touches via
                    dist = self._trace_to_point_distance(trace, via.position)
                    if dist <= via.radius + trace.half_width + 50_000:
                        connected_layers.add(trace.layer_index)

            # Also count pads on this net that touch the via
            for pad in self.board.get_pads_on_net(net):
                for li in pad.layer_indices:
                    if li in via.layer_indices:
                        dist = math.sqrt(pad.position.distance_squared(via.position))
                        if dist <= via.radius + max(pad.size_x, pad.size_y) // 2 + 50_000:
                            connected_layers.add(li)

            if len(connected_layers) <= 1:
                violations.append(DrcViolation(
                    violation_type=ViolationType.SINGLE_LAYER_VIA,
                    severity=Severity.WARNING,
                    message=f"Via connects only {len(connected_layers)} layer(s) — may be redundant",
                    location=via.position,
                    layer_index=via.start_layer,
                    item_ids=(via.id,),
                    net_codes=(via.net_code,),
                ))
        return violations

    # ------------------------------------------------------------------
    # Board edge clearance
    # ------------------------------------------------------------------

    def _check_board_edge_clearance(self) -> list[DrcViolation]:
        """Check that copper items maintain clearance from board edges."""
        violations: list[DrcViolation] = []
        bb = self.board.bounding_box
        if bb.x_min == 0 and bb.y_min == 0 and bb.x_max == 0 and bb.y_max == 0:
            return violations  # No board outline defined

        edge_clearance = self.board.design_rules.board_clearance

        for trace in self.board.get_traces():
            for pt in trace.corners:
                dist = self._distance_to_board_edge(pt, bb)
                if dist - trace.half_width < edge_clearance:
                    violations.append(DrcViolation(
                        violation_type=ViolationType.BOARD_EDGE_CLEARANCE,
                        severity=Severity.ERROR,
                        message=f"Trace too close to board edge ({(dist - trace.half_width) / 1e6:.3f}mm < {edge_clearance / 1e6:.3f}mm)",
                        location=pt,
                        layer_index=trace.layer_index,
                        item_ids=(trace.id,),
                        net_codes=(trace.net_code,),
                        actual_value=dist - trace.half_width,
                        required_value=edge_clearance,
                    ))
                    break  # One violation per trace is enough

        for via in self.board.get_vias():
            dist = self._distance_to_board_edge(via.position, bb)
            if dist - via.radius < edge_clearance:
                violations.append(DrcViolation(
                    violation_type=ViolationType.BOARD_EDGE_CLEARANCE,
                    severity=Severity.ERROR,
                    message=f"Via too close to board edge ({(dist - via.radius) / 1e6:.3f}mm < {edge_clearance / 1e6:.3f}mm)",
                    location=via.position,
                    layer_index=-1,
                    item_ids=(via.id,),
                    net_codes=(via.net_code,),
                    actual_value=dist - via.radius,
                    required_value=edge_clearance,
                ))

        return violations

    # ------------------------------------------------------------------
    # Distance helpers
    # ------------------------------------------------------------------

    def _trace_to_trace_distance(self, t1: Trace, t2: Trace) -> float:
        """Minimum center-line distance between two traces."""
        min_dist = float('inf')
        for i in range(t1.segment_count):
            a1 = t1.corners[i]
            a2 = t1.corners[i + 1]
            for j in range(t2.segment_count):
                b1 = t2.corners[j]
                b2 = t2.corners[j + 1]
                d = segment_clearance_to_segment(a1, a2, b1, b2)
                if d < min_dist:
                    min_dist = d
        return min_dist

    def _trace_to_shape_distance(self, trace: Trace, shape) -> float:
        """Minimum center-line distance from a trace to a shape."""
        if isinstance(shape, IntOctagon):
            min_dist = float('inf')
            for i in range(trace.segment_count):
                p1 = trace.corners[i]
                p2 = trace.corners[i + 1]
                d = segment_clearance_to_octagon(p1, p2, shape)
                if d < min_dist:
                    min_dist = d
            return min_dist
        return float('inf')

    def _trace_to_point_distance(self, trace: Trace, point: IntPoint) -> float:
        """Minimum distance from trace center-line to a point."""
        min_dist = float('inf')
        for i in range(trace.segment_count):
            d = _point_to_segment_dist(point, trace.corners[i], trace.corners[i + 1])
            if d < min_dist:
                min_dist = d
        return min_dist

    def _trace_midpoint(self, trace: Trace) -> IntPoint:
        """Approximate midpoint of a trace (midpoint of first segment)."""
        if trace.segment_count < 1:
            return trace.corners[0] if trace.corners else IntPoint(0, 0)
        p1 = trace.corners[0]
        p2 = trace.corners[1]
        return IntPoint((p1.x + p2.x) // 2, (p1.y + p2.y) // 2)

    def _distance_to_board_edge(self, pt: IntPoint, bb: BoundingBox) -> float:
        """Shortest distance from a point to the board bounding-box edge."""
        return min(
            pt.x - bb.x_min,
            bb.x_max - pt.x,
            pt.y - bb.y_min,
            bb.y_max - pt.y,
        )
