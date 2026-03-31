"""
BatchAutorouter - Multi-pass ripup-and-reroute autorouter.

Orchestrates the complete autorouting process:
1. Identify all unrouted connections
2. For each pass:
   a. Attempt to route each unrouted connection via maze search
   b. Insert found paths as traces/vias on the board
   c. Track board quality score
   d. Increase ripup costs to discourage excessive disruption
3. Stop when fully routed, no improvement, or max passes reached

This replicates Freerouting's BatchAutorouter strategy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from kicad_autorouter.board.board import BoardScore, RoutingBoard
from kicad_autorouter.board.pad import Pad
from kicad_autorouter.board.net import NetClass
from kicad_autorouter.autoroute.engine import AutorouteEngine
from kicad_autorouter.autoroute.maze import MazeSearchAlgo, MazeSearchResult, SearchState
from kicad_autorouter.autoroute.insert import InsertFoundConnectionAlgo
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.utils.timing import TimeLimit

logger = logging.getLogger(__name__)


@dataclass
class AutorouteConfig:
    """Configuration for the batch autorouter."""

    max_passes: int = 20               # Maximum routing passes
    time_limit_seconds: float = 300.0  # Total time budget (5 minutes default)
    initial_ripup_cost: float = 1.0    # Starting ripup cost multiplier
    ripup_cost_increment: float = 2.0  # Multiplier increase per pass
    max_ripup_cost: float = 100.0      # Cap on ripup cost multiplier
    min_improvement_pct: float = 0.5   # Stop if improvement < this % per pass

    # Callbacks
    progress_callback: Callable[[str, float], None] | None = None


@dataclass
class AutorouteResult:
    """Result of a complete autorouting run."""

    completed: bool = False            # True if all connections routed
    passes_run: int = 0
    connections_routed: int = 0
    connections_failed: int = 0
    total_connections: int = 0
    final_score: BoardScore | None = None
    elapsed_seconds: float = 0.0

    @property
    def completion_percentage(self) -> float:
        if self.total_connections == 0:
            return 100.0
        return (self.connections_routed / self.total_connections) * 100.0


class BatchAutorouter:
    """Multi-pass autorouter with ripup-and-reroute.

    This is the top-level autorouting coordinator. It iterates over
    unrouted connections, runs maze search for each, inserts found
    paths, and repeats with increasing ripup penalties until convergence.
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        config: AutorouteConfig | None = None,
    ):
        self.board = board
        self.rules = rules
        self.config = config or AutorouteConfig()
        self.engine = AutorouteEngine(board=board, rules=rules)

    def run(self) -> AutorouteResult:
        """Execute the full batch autorouting process."""
        time_limit = TimeLimit(self.config.time_limit_seconds)
        result = AutorouteResult()

        # Get all connections to route
        connections = self._get_all_connections()
        result.total_connections = len(connections)

        if not connections:
            logger.info("No connections to route")
            result.completed = True
            result.final_score = self.board.compute_score()
            return result

        logger.info("Starting autoroute: %d connections to route", len(connections))
        self._report_progress("Starting autoroute", 0.0)

        best_score = self.board.compute_score()
        ripup_cost = self.config.initial_ripup_cost

        for pass_num in range(1, self.config.max_passes + 1):
            if time_limit.is_expired():
                logger.info("Time limit reached after %d passes", pass_num - 1)
                break

            logger.info(
                "=== Pass %d (ripup_cost=%.1f) ===",
                pass_num, ripup_cost,
            )

            pass_routed = 0
            pass_failed = 0

            # Get currently unrouted connections
            unrouted = self._get_unrouted_connections(connections)
            if not unrouted:
                logger.info("All connections routed after %d passes", pass_num - 1)
                result.completed = True
                break

            for conn_idx, (net_code, source, target) in enumerate(unrouted):
                if time_limit.is_expired():
                    break

                # Build expansion graph
                graph = self.engine.build_expansion_graph(
                    source_pads=[source],
                    target_pads=[target],
                    net_code=net_code,
                )

                # Run maze search
                per_connection_limit = TimeLimit(
                    min(30.0, time_limit.remaining or 30.0)
                )
                search = MazeSearchAlgo(
                    graph=graph,
                    source_pads=[source],
                    target_pads=[target],
                    time_limit=per_connection_limit,
                    ripup_cost_multiplier=ripup_cost,
                )
                search_result = search.find_connection()

                if search_result.state == SearchState.FOUND:
                    # Insert the found path, snapping endpoints to pads
                    inserter = InsertFoundConnectionAlgo(
                        board=self.board,
                        rules=self.rules,
                    )
                    success = inserter.insert(
                        search_result, net_code,
                        source_pad=source, target_pad=target,
                    )
                    if success:
                        pass_routed += 1
                        self.engine.update_after_route(net_code)
                    else:
                        pass_failed += 1
                else:
                    pass_failed += 1

                # Progress
                progress = (conn_idx + 1) / len(unrouted)
                self._report_progress(
                    f"Pass {pass_num}: {pass_routed} routed, {pass_failed} failed",
                    progress,
                )

            # Check improvement
            current_score = self.board.compute_score()
            logger.info(
                "Pass %d result: routed=%d, failed=%d, score=%s",
                pass_num, pass_routed, pass_failed, current_score,
            )

            if current_score.is_better_than(best_score):
                # Check if improvement exceeds minimum threshold
                if (best_score.unrouted_count > 0 and
                        current_score.unrouted_count < best_score.unrouted_count):
                    # Unrouted count decreased — always continue
                    best_score = current_score
                elif best_score.total_trace_length > 0:
                    pct = ((best_score.total_trace_length - current_score.total_trace_length)
                           / best_score.total_trace_length * 100)
                    if pct < self.config.min_improvement_pct and pass_routed == 0:
                        logger.info(
                            "Improvement %.2f%% below threshold %.1f%%, stopping",
                            pct, self.config.min_improvement_pct,
                        )
                        break
                    best_score = current_score
                else:
                    best_score = current_score
            elif pass_routed == 0:
                logger.info("No improvement in pass %d, stopping", pass_num)
                break

            # Increase ripup cost for next pass
            ripup_cost = min(
                ripup_cost * self.config.ripup_cost_increment,
                self.config.max_ripup_cost,
            )
            result.passes_run = pass_num

        # Final tally
        final_unrouted = self._get_unrouted_connections(connections)
        result.connections_routed = result.total_connections - len(final_unrouted)
        result.connections_failed = len(final_unrouted)
        result.completed = len(final_unrouted) == 0
        result.final_score = self.board.compute_score()
        result.elapsed_seconds = time_limit.elapsed

        logger.info(
            "Autoroute complete: %d/%d routed (%.1f%%) in %.1fs",
            result.connections_routed, result.total_connections,
            result.completion_percentage, result.elapsed_seconds,
        )

        return result

    def _get_all_connections(self) -> list[tuple[int, Pad, Pad]]:
        """Get all pad pairs that need routing, ordered by shortest distance first.

        Shortest connections are routed first because they're easier to complete
        and leave more room for longer routes. Power/ground nets are deprioritized
        since they often have many connections and can block signal routes.
        """
        connections = []
        for net_code in self.board.nets:
            if net_code <= 0:
                continue
            pairs = self.board.get_unconnected_pad_pairs(net_code)
            for source, target in pairs:
                connections.append((net_code, source, target))

        # Sort: shortest Manhattan distance first, power nets last
        def _sort_key(conn: tuple[int, Pad, Pad]) -> tuple[int, float]:
            net_code, src, tgt = conn
            net = self.board.nets.get(net_code)
            is_power = 1 if (net and net.is_power) else 0
            dist = abs(src.position.x - tgt.position.x) + abs(src.position.y - tgt.position.y)
            return (is_power, dist)

        connections.sort(key=_sort_key)
        return connections

    def _get_unrouted_connections(
        self,
        all_connections: list[tuple[int, Pad, Pad]],
    ) -> list[tuple[int, Pad, Pad]]:
        """Filter to only unrouted connections."""
        unrouted = []
        for net_code, source, target in all_connections:
            # Check if there's a routed path between source and target
            traces = self.board.get_traces_on_net(net_code)
            if not traces:
                unrouted.append((net_code, source, target))
                continue

            # Connectivity check with proper tolerance:
            # A pad is "connected" if any trace endpoint lands within
            # pad_radius + trace_half_width of the pad center.
            source_connected = False
            target_connected = False
            for trace in traces:
                if trace.first_corner is None or trace.last_corner is None:
                    continue
                hw = trace.width // 2
                for corner in [trace.first_corner, trace.last_corner]:
                    src_tol = (max(source.size_x, source.size_y) // 2 + hw) ** 2
                    tgt_tol = (max(target.size_x, target.size_y) // 2 + hw) ** 2
                    if source.position.distance_squared(corner) <= src_tol:
                        source_connected = True
                    if target.position.distance_squared(corner) <= tgt_tol:
                        target_connected = True

            if not (source_connected and target_connected):
                unrouted.append((net_code, source, target))

        return unrouted

    def _report_progress(self, message: str, fraction: float):
        """Report progress via callback."""
        if self.config.progress_callback:
            self.config.progress_callback(message, fraction)
        logger.debug("Progress: %s (%.1f%%)", message, fraction * 100)
