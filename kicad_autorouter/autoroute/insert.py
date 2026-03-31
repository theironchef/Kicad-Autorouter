"""
InsertFoundConnectionAlgo - Converts maze search results into board traces/vias.

Takes the waypoint path from MazeSearchResult and creates actual Trace and Via
items on the RoutingBoard. Handles 45-degree snapping, corner cleanup, and
via insertion at layer transitions.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.direction import Direction45
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.pad import Pad
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.via import Via
from kicad_autorouter.autoroute.maze import MazeSearchResult
from kicad_autorouter.rules.design_rules import DesignRules

logger = logging.getLogger(__name__)


@dataclass
class InsertFoundConnectionAlgo:
    """Converts a maze search path into traces and vias on the board.

    Takes raw waypoints from the maze search and:
    1. Snaps to 45-degree angles (if configured)
    2. Removes redundant intermediate points
    3. Creates trace segments on each layer
    4. Inserts vias at layer transitions
    """

    board: RoutingBoard
    rules: DesignRules

    def insert(
        self,
        search_result: MazeSearchResult,
        net_code: int,
        source_pad: Pad | None = None,
        target_pad: Pad | None = None,
    ) -> bool:
        """Insert the found path into the board. Returns True on success.

        If source_pad / target_pad are provided, the first and last waypoints
        are snapped to the exact pad positions so the trace actually connects.
        """
        if not search_result.waypoints or len(search_result.waypoints) < 2:
            return False

        net_class = self.board.get_net_class_for_net(net_code)
        trace_width = self.rules.get_trace_width(net_class)
        via_diameter = self.rules.get_via_diameter(net_class)
        via_drill = self.rules.get_via_drill(net_class)

        waypoints = list(search_result.waypoints)
        layers = list(search_result.waypoint_layers)

        # Snap endpoints to actual pad positions so connectivity checks pass
        if source_pad is not None:
            waypoints[0] = source_pad.position
        if target_pad is not None:
            waypoints[-1] = target_pad.position

        # Clean up path: snap to 45-degree grid and remove collinear points
        if self.rules.prefer_45_degree:
            waypoints = self._snap_to_45(waypoints)

        waypoints = self._remove_collinear(waypoints, layers)

        # Create traces and vias
        current_layer = layers[0]
        trace_corners: list[IntPoint] = [waypoints[0]]

        for i in range(1, len(waypoints)):
            next_layer = layers[min(i, len(layers) - 1)]

            if next_layer != current_layer:
                # Layer transition: finish current trace, insert via
                if len(trace_corners) >= 2:
                    self.board.add_trace(
                        corners=trace_corners[:],
                        width=trace_width,
                        layer_index=current_layer,
                        net_code=net_code,
                    )

                # Insert via at the transition point, selecting type
                via_pos = waypoints[i - 1] if i > 0 else waypoints[i]
                sl = min(current_layer, next_layer)
                el = max(current_layer, next_layer)
                total_layers = self.board.layer_structure.copper_layer_count
                v_diam, v_drill, _is_micro = self.rules.select_via_type(
                    sl, el, total_layers, net_class,
                )
                self.board.add_via(
                    position=via_pos,
                    diameter=v_diam,
                    drill=v_drill,
                    start_layer=sl,
                    end_layer=el,
                    net_code=net_code,
                )

                # Start new trace on the new layer
                current_layer = next_layer
                trace_corners = [via_pos]

            trace_corners.append(waypoints[i])

        # Finish the last trace segment
        if len(trace_corners) >= 2:
            self.board.add_trace(
                corners=trace_corners,
                width=trace_width,
                layer_index=current_layer,
                net_code=net_code,
            )

        return True

    def _snap_to_45(self, waypoints: list[IntPoint]) -> list[IntPoint]:
        """Snap waypoints to 45-degree routing grid.

        Adjusts intermediate points so all segments are at 0, 45, 90, 135
        degree angles. This produces cleaner, more professional routing.
        """
        if len(waypoints) <= 2:
            return waypoints

        result = [waypoints[0]]

        for i in range(1, len(waypoints) - 1):
            prev = result[-1]
            curr = waypoints[i]
            next_pt = waypoints[i + 1]

            # Snap to nearest 45-degree direction
            snapped = self._snap_point_45(prev, curr, next_pt)
            result.append(snapped)

        result.append(waypoints[-1])
        return result

    def _snap_point_45(
        self, prev: IntPoint, curr: IntPoint, next_pt: IntPoint,
    ) -> IntPoint:
        """Snap a single intermediate point to create 45-degree segments."""
        dx = curr.x - prev.x
        dy = curr.y - prev.y

        # Find the nearest 45-degree direction
        angle = math.atan2(dy, dx)
        snapped_angle = round(angle / (math.pi / 4)) * (math.pi / 4)

        # Project onto the 45-degree line
        length = math.sqrt(dx * dx + dy * dy)
        new_x = prev.x + round(length * math.cos(snapped_angle))
        new_y = prev.y + round(length * math.sin(snapped_angle))

        return IntPoint(new_x, new_y)

    def _remove_collinear(
        self,
        waypoints: list[IntPoint],
        layers: list[int],
    ) -> list[IntPoint]:
        """Remove intermediate points that are collinear with their neighbors."""
        if len(waypoints) <= 2:
            return waypoints

        result = [waypoints[0]]
        result_layers = [layers[0]]

        for i in range(1, len(waypoints) - 1):
            prev = result[-1]
            curr = waypoints[i]
            next_pt = waypoints[i + 1]
            curr_layer = layers[min(i, len(layers) - 1)]
            next_layer = layers[min(i + 1, len(layers) - 1)]

            # Keep if layer changes at this point
            if curr_layer != result_layers[-1] or curr_layer != next_layer:
                result.append(curr)
                result_layers.append(curr_layer)
                continue

            # Check collinearity using cross product
            cross = ((curr.x - prev.x) * (next_pt.y - prev.y) -
                     (curr.y - prev.y) * (next_pt.x - prev.x))
            if abs(cross) > 100:  # Small tolerance for rounding
                result.append(curr)
                result_layers.append(curr_layer)

        result.append(waypoints[-1])
        return result
