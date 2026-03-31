"""
Batch optimizer — sequential and multi-threaded post-route optimization.

Coordinates pull-tight, via reduction, and corner smoothing across all
traces, optionally using a thread pool for parallelism.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from threading import Lock

from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.optimize.pull_tight import PullTightAlgo, PullTightConfig
from kicad_autorouter.optimize.via_optimize import ViaOptimizer, ViaOptConfig
from kicad_autorouter.utils.timing import TimeLimit

logger = logging.getLogger(__name__)


@dataclass
class BatchOptConfig:
    """Configuration for batch optimization."""

    max_passes: int = 3
    pull_tight: bool = True
    remove_vias: bool = True
    time_limit_seconds: float = 120.0

    # Thread pool (0 = sequential, >0 = that many workers)
    num_threads: int = 0


@dataclass
class BatchOptResult:
    """Results of a batch optimization run."""

    traces_improved: int = 0
    vias_removed: int = 0
    passes_run: int = 0
    elapsed_seconds: float = 0.0


class BatchOptimizer:
    """Sequential batch optimizer — runs pull-tight and via removal."""

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        config: BatchOptConfig | None = None,
    ):
        self.board = board
        self.rules = rules
        self.config = config or BatchOptConfig()

    def run(self) -> BatchOptResult:
        time_limit = TimeLimit(self.config.time_limit_seconds)
        result = BatchOptResult()

        for pass_num in range(1, self.config.max_passes + 1):
            if time_limit.is_expired():
                break

            improved = 0

            if self.config.pull_tight:
                pt = PullTightAlgo(self.board, self.rules)
                improved += pt.optimize_all()

            if self.config.remove_vias:
                vo = ViaOptimizer(self.board, self.rules)
                removed = vo.optimize_all()
                result.vias_removed += removed
                improved += removed

            result.traces_improved += improved
            result.passes_run = pass_num

            if improved == 0:
                break

        result.elapsed_seconds = time_limit.elapsed
        logger.info(
            "BatchOptimizer: %d traces improved, %d vias removed in %d passes (%.1fs)",
            result.traces_improved, result.vias_removed,
            result.passes_run, result.elapsed_seconds,
        )
        return result


class BatchOptimizerMultiThreaded:
    """Multi-threaded batch optimizer.

    Partitions traces by net so each thread works on independent nets.
    Thread-safe: each worker only modifies traces on its assigned net.
    Board-level reads (for collision queries) use a shared lock.
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        config: BatchOptConfig | None = None,
    ):
        self.board = board
        self.rules = rules
        self.config = config or BatchOptConfig(num_threads=4)
        self._board_lock = Lock()

    def run(self) -> BatchOptResult:
        num_threads = max(1, self.config.num_threads)
        time_limit = TimeLimit(self.config.time_limit_seconds)
        result = BatchOptResult()

        # Group traces by net for independent processing
        net_traces = self._group_traces_by_net()

        if not net_traces:
            return result

        for pass_num in range(1, self.config.max_passes + 1):
            if time_limit.is_expired():
                break

            pass_improved = 0

            if self.config.pull_tight:
                pass_improved += self._parallel_pull_tight(
                    net_traces, num_threads, time_limit,
                )

            # Via removal is sequential (modifies board topology)
            if self.config.remove_vias and not time_limit.is_expired():
                vo = ViaOptimizer(self.board, self.rules)
                removed = vo.optimize_all()
                result.vias_removed += removed
                pass_improved += removed

            result.traces_improved += pass_improved
            result.passes_run = pass_num

            if pass_improved == 0:
                break

        result.elapsed_seconds = time_limit.elapsed
        logger.info(
            "BatchOptimizerMT(%d threads): %d traces improved, "
            "%d vias removed in %d passes (%.1fs)",
            num_threads, result.traces_improved, result.vias_removed,
            result.passes_run, result.elapsed_seconds,
        )
        return result

    def _group_traces_by_net(self) -> dict[int, list[Trace]]:
        """Group non-fixed traces by net code."""
        groups: dict[int, list[Trace]] = {}
        for trace in self.board.get_traces():
            if trace.is_fixed:
                continue
            nc = trace.net_code
            groups.setdefault(nc, []).append(trace)
        return groups

    def _parallel_pull_tight(
        self,
        net_traces: dict[int, list[Trace]],
        num_threads: int,
        time_limit: TimeLimit,
    ) -> int:
        """Run pull-tight on each net in parallel."""
        improved = 0

        def _optimize_net(net_code: int, traces: list[Trace]) -> int:
            """Worker function for one net's traces."""
            if time_limit.is_expired():
                return 0
            count = 0
            # Each worker creates its own PullTightAlgo with a fresh tree
            with self._board_lock:
                pt = PullTightAlgo(
                    self.board, self.rules,
                    PullTightConfig(time_limit_seconds=min(
                        30.0, time_limit.remaining or 30.0,
                    )),
                )
            for trace in traces:
                if time_limit.is_expired():
                    break
                if pt._optimize_trace(trace):
                    count += 1
            return count

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = {
                pool.submit(_optimize_net, nc, traces): nc
                for nc, traces in net_traces.items()
            }
            for future in as_completed(futures):
                try:
                    improved += future.result()
                except Exception:
                    logger.exception("Error in parallel pull-tight for net %s",
                                     futures[future])

        return improved
