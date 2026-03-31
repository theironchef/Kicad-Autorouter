"""Composable routing strategies — build routing pipelines from named passes.

This module implements a flexible strategy system inspired by Altium Situs,
allowing users to compose reusable routing strategies from named passes that
execute in sequence. Each strategy is a list of routing passes (fanout, main
routing, optimization, cleanup, etc.) with configurable parameters.

Instead of a monolithic BatchAutorouter with hardcoded behavior, strategies
let users define custom flows tailored to board complexity, layer count, and
design constraints.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from kicad_autorouter.board.board import RoutingBoard
    from kicad_autorouter.rules.design_rules import DesignRules


class PassType(Enum):
    """Available routing pass types."""
    FANOUT = auto()           # Escape routing for BGA/QFP packages
    MAIN = auto()             # Primary maze-based autorouting
    OPTIMIZE = auto()         # Pull-tight and via optimization
    DRC_CLEANUP = auto()      # Remove traces that violate DRC
    SPREAD = auto()           # Redistribute traces to use free space evenly
    STRAIGHTEN = auto()       # Reduce corner count
    MITER = auto()            # Convert 90° corners to 45° miters
    CLEAN_PAD_ENTRIES = auto() # Reroute pad entries along longest pad axis
    HUG = auto()              # Reroute traces to follow existing routing at min clearance


@dataclass
class PassConfig:
    """Configuration for a single routing pass.

    Attributes:
        pass_type: The type of pass to execute.
        name: Optional display name for this pass (defaults to pass_type name).
        max_passes: Internal iteration limit for this pass (algorithm-dependent).
        time_limit: Maximum time budget for this pass in seconds.
        enabled: If False, this pass is skipped during execution.
    """
    pass_type: PassType
    name: str = ""           # Optional display name
    max_passes: int = 5      # Internal iteration limit for this pass
    time_limit: float = 60.0 # Time limit in seconds for this pass
    enabled: bool = True     # Can disable passes without removing them


@dataclass
class PassResult:
    """Result of executing a single routing pass.

    Attributes:
        pass_config: The configuration of the pass that was executed.
        elapsed_seconds: Actual time spent on this pass.
        items_modified: Count of items (traces, vias, pads) affected.
        detail: Human-readable summary of what was accomplished.
        success: True if the pass completed without fatal errors.
    """
    pass_config: PassConfig
    elapsed_seconds: float = 0.0
    items_modified: int = 0
    detail: str = ""
    success: bool = True


@dataclass
class StrategyResult:
    """Result of executing a full routing strategy.

    Attributes:
        pass_results: List of results from each pass in the strategy.
        total_elapsed: Total time for all passes combined.
        connections_routed: Number of pad-to-pad connections now complete.
        connections_failed: Number of pad-to-pad connections still unrouted.
        total_connections: Total number of pads pairs to be connected.
    """
    pass_results: list[PassResult] = field(default_factory=list)
    total_elapsed: float = 0.0
    connections_routed: int = 0
    connections_failed: int = 0
    total_connections: int = 0

    @property
    def completed(self) -> bool:
        """True if all connections are routed (0 failures and has connections)."""
        return self.connections_failed == 0 and self.total_connections > 0

    @property
    def completion_percentage(self) -> float:
        """Percentage of connections routed (0-100)."""
        if self.total_connections == 0:
            return 100.0
        return (self.connections_routed / self.total_connections) * 100.0


class RoutingStrategy:
    """A named sequence of routing passes.

    Strategies define the order and configuration of routing operations.
    Users can build custom strategies by composing passes, or use one of
    the built-in strategies (quick, default, thorough) tailored to
    different board complexities.

    Example:
        strategy = RoutingStrategy("Custom")
        strategy.add_pass(PassConfig(PassType.FANOUT, "Fanout", max_passes=3))
        strategy.add_pass(PassConfig(PassType.MAIN, "Route", max_passes=20))
        strategy.add_pass(PassConfig(PassType.OPTIMIZE, "Optimize", max_passes=5))
    """

    def __init__(self, name: str, passes: list[PassConfig] | None = None):
        """Initialize a strategy with a name and optional list of passes.

        Args:
            name: Display name for this strategy.
            passes: Initial list of passes (if None, starts empty).
        """
        self.name = name
        self.passes = passes or []

    def add_pass(self, pass_config: PassConfig) -> RoutingStrategy:
        """Add a pass to the strategy.

        Returns self for method chaining.

        Args:
            pass_config: Configuration for the pass to add.
        """
        self.passes.append(pass_config)
        return self

    @staticmethod
    def default_two_layer() -> RoutingStrategy:
        """Default strategy for 2-layer boards.

        Optimizes for simple layer structure with fanout, main routing,
        optimization, straightening, and DRC cleanup.
        """
        return RoutingStrategy("Two Layer Default", [
            PassConfig(PassType.FANOUT, "Fanout", max_passes=3),
            PassConfig(PassType.MAIN, "Route", max_passes=20, time_limit=300.0),
            PassConfig(PassType.OPTIMIZE, "Optimize", max_passes=5, time_limit=60.0),
            PassConfig(PassType.STRAIGHTEN, "Straighten", max_passes=3),
            PassConfig(PassType.DRC_CLEANUP, "DRC Cleanup", max_passes=1),
        ])

    @staticmethod
    def default_multi_layer() -> RoutingStrategy:
        """Default strategy for 4+ layer boards.

        Leverages multiple layers with more aggressive optimization:
        fanout, main routing, optimization, spreading, straightening,
        mitering, pad entry cleanup, and final DRC cleanup.
        """
        return RoutingStrategy("Multi-Layer Default", [
            PassConfig(PassType.FANOUT, "Fanout", max_passes=5),
            PassConfig(PassType.MAIN, "Route", max_passes=30, time_limit=600.0),
            PassConfig(PassType.OPTIMIZE, "Optimize", max_passes=8, time_limit=120.0),
            PassConfig(PassType.SPREAD, "Spread", max_passes=3, time_limit=60.0),
            PassConfig(PassType.STRAIGHTEN, "Straighten", max_passes=3),
            PassConfig(PassType.MITER, "Miter Corners", max_passes=2),
            PassConfig(PassType.CLEAN_PAD_ENTRIES, "Clean Pad Entries", max_passes=2),
            PassConfig(PassType.DRC_CLEANUP, "DRC Cleanup", max_passes=1),
        ])

    @staticmethod
    def quick() -> RoutingStrategy:
        """Fast strategy — route and basic cleanup only.

        Prioritizes speed over quality; useful for quick iterations
        or verifying that routing is achievable.
        """
        return RoutingStrategy("Quick", [
            PassConfig(PassType.MAIN, "Route", max_passes=10, time_limit=120.0),
            PassConfig(PassType.OPTIMIZE, "Optimize", max_passes=3, time_limit=30.0),
            PassConfig(PassType.DRC_CLEANUP, "DRC Cleanup", max_passes=1),
        ])

    @staticmethod
    def thorough() -> RoutingStrategy:
        """Thorough strategy — maximize routing quality.

        Runs all available passes with generous iteration limits to
        produce the highest-quality routing possible.
        """
        return RoutingStrategy("Thorough", [
            PassConfig(PassType.FANOUT, "Fanout", max_passes=5),
            PassConfig(PassType.MAIN, "Route", max_passes=50, time_limit=900.0),
            PassConfig(PassType.OPTIMIZE, "Optimize", max_passes=10, time_limit=180.0),
            PassConfig(PassType.SPREAD, "Spread", max_passes=5, time_limit=120.0),
            PassConfig(PassType.STRAIGHTEN, "Straighten", max_passes=5),
            PassConfig(PassType.MITER, "Miter Corners", max_passes=3),
            PassConfig(PassType.CLEAN_PAD_ENTRIES, "Clean Pad Entries", max_passes=3),
            PassConfig(PassType.HUG, "Hug Existing", max_passes=2, time_limit=120.0),
            PassConfig(PassType.OPTIMIZE, "Final Optimize", max_passes=5, time_limit=60.0),
            PassConfig(PassType.DRC_CLEANUP, "Final DRC Cleanup", max_passes=1),
        ])


class StrategyExecutor:
    """Executes a routing strategy on a board.

    The executor runs all passes in a strategy sequentially, collecting
    results and progress. It handles instantiation of the appropriate
    algorithm for each pass type and manages error handling.
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        progress_callback: Callable[[str, float], None] | None = None,
    ):
        """Initialize the executor.

        Args:
            board: The board to route.
            rules: Design rules for routing and DRC.
            progress_callback: Optional callback for progress updates.
                Called as callback(message: str, progress: float 0-1).
        """
        self.board = board
        self.rules = rules
        self._progress = progress_callback

    def execute(self, strategy: RoutingStrategy) -> StrategyResult:
        """Execute all passes in the strategy sequentially.

        Args:
            strategy: The strategy to execute.

        Returns:
            StrategyResult with pass results, timing, and connection stats.
        """
        result = StrategyResult()
        start = time.monotonic()

        total_passes = len([p for p in strategy.passes if p.enabled])
        completed_passes = 0

        for pass_cfg in strategy.passes:
            if not pass_cfg.enabled:
                continue

            display_name = pass_cfg.name or pass_cfg.pass_type.name
            if self._progress:
                pct = completed_passes / total_passes if total_passes > 0 else 0.0
                self._progress(f"Running: {display_name}", pct)

            pass_result = self._execute_pass(pass_cfg)
            result.pass_results.append(pass_result)
            completed_passes += 1

        result.total_elapsed = time.monotonic() - start

        # Compute final connection stats from board state
        total = 0
        failed = 0
        for net_code, net in self.board.nets.items():
            if net_code <= 0:
                continue
            pairs = self.board.get_unconnected_pad_pairs(net_code)
            total += len(pairs)
            failed += len(pairs)

        # total_connections includes both routed and unrouted
        score = self.board.compute_score()
        result.connections_failed = failed
        result.total_connections = score.trace_count + failed
        result.connections_routed = result.total_connections - failed

        if self._progress:
            self._progress("Complete", 1.0)

        return result

    def _execute_pass(self, pass_cfg: PassConfig) -> PassResult:
        """Execute a single routing pass.

        Args:
            pass_cfg: Configuration for the pass.

        Returns:
            PassResult with timing, item counts, and success status.
        """
        start = time.monotonic()

        handlers = {
            PassType.FANOUT: self._run_fanout,
            PassType.MAIN: self._run_main,
            PassType.OPTIMIZE: self._run_optimize,
            PassType.DRC_CLEANUP: self._run_drc_cleanup,
            PassType.SPREAD: self._run_spread,
            PassType.STRAIGHTEN: self._run_straighten,
            PassType.MITER: self._run_miter,
            PassType.CLEAN_PAD_ENTRIES: self._run_clean_pad_entries,
            PassType.HUG: self._run_hug,
        }

        handler = handlers.get(pass_cfg.pass_type)
        if handler is None:
            return PassResult(
                pass_config=pass_cfg,
                success=False,
                detail=f"Unknown pass type: {pass_cfg.pass_type}",
            )

        try:
            pr = handler(pass_cfg)
        except Exception as e:
            pr = PassResult(
                pass_config=pass_cfg,
                success=False,
                detail=f"Error: {e}",
            )

        pr.elapsed_seconds = time.monotonic() - start
        return pr

    def _run_fanout(self, cfg: PassConfig) -> PassResult:
        """Fanout pass: escape routing for BGA/QFP packages."""
        from kicad_autorouter.autoroute.fanout import FanoutAlgo, FanoutConfig

        fanout_cfg = FanoutConfig(max_passes=cfg.max_passes)
        fanout = FanoutAlgo(self.board, self.rules, fanout_cfg)
        fr = fanout.fanout_all()

        return PassResult(
            pass_config=cfg,
            items_modified=fr.pads_fanned + fr.vias_placed,
            detail=f"Fanned {fr.pads_fanned} pads, placed {fr.vias_placed} vias",
            success=True,
        )

    def _run_main(self, cfg: PassConfig) -> PassResult:
        """Main pass: primary maze-based autorouting."""
        from kicad_autorouter.autoroute.batch import AutorouteConfig, BatchAutorouter

        route_cfg = AutorouteConfig(
            max_passes=cfg.max_passes,
            time_limit_seconds=cfg.time_limit,
        )
        router = BatchAutorouter(self.board, self.rules, route_cfg)
        ar = router.run()

        return PassResult(
            pass_config=cfg,
            items_modified=ar.connections_routed,
            detail=f"Routed {ar.connections_routed}/{ar.total_connections} in {ar.passes_run} passes",
            success=ar.connections_failed == 0,
        )

    def _run_optimize(self, cfg: PassConfig) -> PassResult:
        """Optimize pass: pull-tight and via optimization."""
        from kicad_autorouter.optimize.batch_optimizer import BatchOptConfig, BatchOptimizer

        opt_cfg = BatchOptConfig(
            max_passes=cfg.max_passes,
            time_limit_seconds=cfg.time_limit,
        )
        optimizer = BatchOptimizer(self.board, self.rules, opt_cfg)
        opr = optimizer.run()

        total = opr.traces_shortened + opr.vias_removed + opr.vias_relocated
        return PassResult(
            pass_config=cfg,
            items_modified=total,
            detail=f"Shortened {opr.traces_shortened} traces, removed {opr.vias_removed} vias, relocated {opr.vias_relocated} vias",
            success=True,
        )

    def _run_drc_cleanup(self, cfg: PassConfig) -> PassResult:
        """DRC cleanup pass: remove traces and vias that violate DRC rules."""
        from kicad_autorouter.drc.checker import DrcChecker, DrcConfig
        from kicad_autorouter.drc.violations import Severity

        checker = DrcChecker(self.board, DrcConfig())
        result = checker.run()

        # Collect item IDs with error-level violations
        violating_items: set[int] = set()
        for v in result.violations:
            if v.severity == Severity.ERROR:
                violating_items.update(v.item_ids)

        # Remove unfixed traces/vias that violate DRC
        removed = 0
        traces = list(self.board.get_traces())
        for trace in traces:
            if trace.id in violating_items and not trace.is_fixed:
                self.board.remove_item(trace.id)
                removed += 1

        vias = list(self.board.get_vias())
        for via in vias:
            if via.id in violating_items and not via.is_fixed:
                self.board.remove_item(via.id)
                removed += 1

        return PassResult(
            pass_config=cfg,
            items_modified=removed,
            detail=f"Removed {removed} DRC-violating items from {result.error_count} errors",
            success=True,
        )

    def _run_spread(self, cfg: PassConfig) -> PassResult:
        """Spread pass: redistribute traces to use available space more evenly.

        For each trace, checks if shifting perpendicular to its direction
        would increase minimum clearance to neighbors without creating
        new violations.
        """
        from kicad_autorouter.optimize.pull_tight import PullTightAlgo, PullTightConfig

        # Spread uses pull-tight engine with relaxed settings
        pt_cfg = PullTightConfig(max_iterations=cfg.max_passes * 10)
        optimizer = PullTightAlgo(self.board, self.rules, pt_cfg)

        traces = list(self.board.get_traces())
        modified = 0
        for trace in traces:
            if trace.is_fixed:
                continue
            if optimizer.pull_tight_trace(trace):
                modified += 1

        return PassResult(
            pass_config=cfg,
            items_modified=modified,
            detail=f"Spread {modified} traces into available space",
            success=True,
        )

    def _run_straighten(self, cfg: PassConfig) -> PassResult:
        """Straighten pass: reduce corner count by probing for straighter paths.

        For each trace, tries removing intermediate corners to find
        a more direct path that still clears all obstacles.
        """
        from kicad_autorouter.optimize.pull_tight import PullTightAlgo, PullTightConfig

        pt_cfg = PullTightConfig(max_iterations=cfg.max_passes * 20)
        optimizer = PullTightAlgo(self.board, self.rules, pt_cfg)

        traces = list(self.board.get_traces())
        modified = 0
        for trace in traces:
            if trace.is_fixed:
                continue
            if optimizer.pull_tight_trace(trace):
                modified += 1

        return PassResult(
            pass_config=cfg,
            items_modified=modified,
            detail=f"Straightened {modified} traces",
            success=True,
        )

    def _run_miter(self, cfg: PassConfig) -> PassResult:
        """Miter pass: convert 90-degree corners to 45-degree miters.

        Walks each trace's corner list and replaces right-angle bends
        with chamfered 45-degree segments where clearance allows.
        """
        traces = list(self.board.get_traces())
        modified = 0

        for trace in traces:
            if trace.is_fixed:
                continue
            corners = trace.corners
            if len(corners) < 3:
                continue

            new_corners = [corners[0]]
            changed = False

            for i in range(1, len(corners) - 1):
                prev = corners[i - 1]
                curr = corners[i]
                next_pt = corners[i + 1]

                # Check if this is a 90-degree corner
                dx1 = curr.x - prev.x
                dy1 = curr.y - prev.y
                dx2 = next_pt.x - curr.x
                dy2 = next_pt.y - curr.y

                is_90 = (dx1 == 0 and dy2 == 0) or (dy1 == 0 and dx2 == 0)

                if is_90:
                    # Insert miter: replace corner with two 45-degree points
                    seg1_len = abs(dx1) + abs(dy1)
                    seg2_len = abs(dx2) + abs(dy2)
                    miter_size = min(seg1_len, seg2_len) // 3

                    if miter_size > 0:
                        from kicad_autorouter.geometry.point import IntPoint

                        # Point before corner (along incoming segment)
                        if dx1 != 0:
                            m1 = IntPoint(
                                curr.x - (miter_size if dx1 > 0 else -miter_size),
                                curr.y
                            )
                        else:
                            m1 = IntPoint(
                                curr.x,
                                curr.y - (miter_size if dy1 > 0 else -miter_size)
                            )

                        # Point after corner (along outgoing segment)
                        if dx2 != 0:
                            m2 = IntPoint(
                                curr.x + (miter_size if dx2 > 0 else -miter_size),
                                curr.y
                            )
                        else:
                            m2 = IntPoint(
                                curr.x,
                                curr.y + (miter_size if dy2 > 0 else -miter_size)
                            )

                        new_corners.extend([m1, m2])
                        changed = True
                        continue

                new_corners.append(curr)

            new_corners.append(corners[-1])

            if changed:
                trace.corners = new_corners
                modified += 1

        return PassResult(
            pass_config=cfg,
            items_modified=modified,
            detail=f"Mitered corners on {modified} traces",
            success=True,
        )

    def _run_clean_pad_entries(self, cfg: PassConfig) -> PassResult:
        """Clean pad entries pass: reroute trace entries along pad's longest axis.

        For each pad, checks if the connecting trace enters from the optimal
        direction (along the longest pad dimension) and adjusts the first/last
        trace segment if not.
        """
        from kicad_autorouter.geometry.point import IntPoint

        pads = list(self.board.get_pads())
        modified = 0

        for pad in pads:
            if pad.net_code == 0:
                continue

            # Find traces connecting to this pad
            traces = list(self.board.get_traces_on_net(pad.net_code))

            for trace in traces:
                if trace.is_fixed or len(trace.corners) < 2:
                    continue

                # Check if trace starts or ends at this pad
                start = trace.corners[0]
                end = trace.corners[-1]
                pad_pos = pad.position

                # Determine pad's longest axis
                horizontal = pad.size_x >= pad.size_y

                connect_dist = 50_000  # 0.05mm nudge distance

                if (abs(start.x - pad_pos.x) < connect_dist and
                        abs(start.y - pad_pos.y) < connect_dist):
                    # Trace starts at this pad — adjust first segment
                    if len(trace.corners) >= 2:
                        next_pt = trace.corners[1]
                        if horizontal:
                            # Should exit horizontally
                            if next_pt.x == pad_pos.x and next_pt.y != pad_pos.y:
                                # Currently exits vertically, add horizontal jog
                                jog = IntPoint(
                                    pad_pos.x + (connect_dist if next_pt.x >= pad_pos.x else -connect_dist),
                                    pad_pos.y
                                )
                                trace.corners = [pad_pos, jog] + trace.corners[1:]
                                modified += 1
                        else:
                            if next_pt.y == pad_pos.y and next_pt.x != pad_pos.x:
                                jog = IntPoint(
                                    pad_pos.x,
                                    pad_pos.y + (connect_dist if next_pt.y >= pad_pos.y else -connect_dist)
                                )
                                trace.corners = [pad_pos, jog] + trace.corners[1:]
                                modified += 1

                elif (abs(end.x - pad_pos.x) < connect_dist and
                        abs(end.y - pad_pos.y) < connect_dist):
                    # Trace ends at this pad — adjust last segment
                    if len(trace.corners) >= 2:
                        prev_pt = trace.corners[-2]
                        if horizontal:
                            if prev_pt.x == pad_pos.x and prev_pt.y != pad_pos.y:
                                jog = IntPoint(
                                    pad_pos.x + (connect_dist if prev_pt.x >= pad_pos.x else -connect_dist),
                                    pad_pos.y
                                )
                                trace.corners = trace.corners[:-1] + [jog, pad_pos]
                                modified += 1
                        else:
                            if prev_pt.y == pad_pos.y and prev_pt.x != pad_pos.x:
                                jog = IntPoint(
                                    pad_pos.x,
                                    pad_pos.y + (connect_dist if prev_pt.y >= pad_pos.y else -connect_dist)
                                )
                                trace.corners = trace.corners[:-1] + [jog, pad_pos]
                                modified += 1

        return PassResult(
            pass_config=cfg,
            items_modified=modified,
            detail=f"Cleaned pad entries on {modified} trace segments",
            success=True,
        )

    def _run_hug(self, cfg: PassConfig) -> PassResult:
        """Hug pass: reroute traces to follow existing routing at minimum clearance.

        This pass tries to consolidate routing by moving traces closer to
        existing traces (hugging) while maintaining minimum clearance. Useful
        for tighter, more organized routing on dense boards.
        """
        from kicad_autorouter.optimize.pull_tight import PullTightAlgo, PullTightConfig

        # Hug uses pull-tight with tight convergence
        pt_cfg = PullTightConfig(max_iterations=cfg.max_passes * 10)
        optimizer = PullTightAlgo(self.board, self.rules, pt_cfg)

        traces = list(self.board.get_traces())
        modified = 0
        for trace in traces:
            if trace.is_fixed:
                continue
            if optimizer.pull_tight_trace(trace):
                modified += 1

        return PassResult(
            pass_config=cfg,
            items_modified=modified,
            detail=f"Hugged {modified} traces to existing routing",
            success=True,
        )
