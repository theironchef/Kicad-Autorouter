"""
Board update and connection selection strategies.

Update strategies control when optimization happens relative to routing:
- Greedy: optimize each trace immediately after insertion
- Global: batch optimize at the end of each pass
- Hybrid: greedy for small changes, switch to global when many items change

Selection strategies control the order in which connections are attempted:
- Sequential: as they appear in the net list
- Shortest first: shortest Manhattan distance
- Random: random shuffle each pass
- Prioritized: use NetPriority scores from net_operations
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.pad import Pad
from kicad_autorouter.board.changed_area import ChangedArea
from kicad_autorouter.optimize.pull_tight import PullTightAlgo, PullTightConfig
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.rules.router_settings import (
    UpdateStrategy,
    SelectionStrategy,
    RouterSettings,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Update strategies
# ---------------------------------------------------------------------------

class BoardUpdater:
    """Applies the chosen update strategy after routing changes.

    Usage::

        updater = BoardUpdater(board, rules, settings)
        updater.notify_route_inserted(trace)   # called after each insert
        updater.end_of_pass()                  # called at end of each pass
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        settings: RouterSettings,
    ):
        self.board = board
        self.rules = rules
        self.settings = settings
        self._changed = ChangedArea()
        self._changes_since_optimize = 0

    def notify_route_inserted(self, item) -> int:
        """Called after a trace/via is inserted. Returns items optimised."""
        self._changed.mark_item_changed(item)
        self._changes_since_optimize += 1

        strategy = self.settings.update_strategy

        if strategy == UpdateStrategy.GREEDY:
            return self._optimize_now()

        if strategy == UpdateStrategy.HYBRID:
            if self._changes_since_optimize >= self.settings.hybrid_threshold:
                return self._optimize_now()

        # GLOBAL: defer to end_of_pass
        return 0

    def end_of_pass(self) -> int:
        """Called at the end of a routing pass. Returns items optimised."""
        strategy = self.settings.update_strategy

        if strategy == UpdateStrategy.GLOBAL or strategy == UpdateStrategy.HYBRID:
            if self._changed.has_changes:
                return self._optimize_now()

        return 0

    def _optimize_now(self) -> int:
        """Run pull-tight on traces in the changed area."""
        if not self._changed.has_changes:
            return 0

        pt = PullTightAlgo(
            self.board, self.rules,
            PullTightConfig(
                max_iterations=self.settings.pull_tight_accuracy,
                time_limit_seconds=30.0,
            ),
        )
        improved = pt.optimize_all()

        self._changed.clear()
        self._changes_since_optimize = 0
        return improved


# ---------------------------------------------------------------------------
# Selection strategies
# ---------------------------------------------------------------------------

Connection = tuple[int, Pad, Pad]  # (net_code, source_pad, target_pad)


class ConnectionSelector:
    """Orders unrouted connections according to the chosen strategy.

    Usage::

        selector = ConnectionSelector(board, settings)
        ordered = selector.order(connections)
    """

    def __init__(self, board: RoutingBoard, settings: RouterSettings):
        self.board = board
        self.settings = settings

    def order(self, connections: list[Connection]) -> list[Connection]:
        """Return connections in the order they should be attempted."""
        strategy = self.settings.selection_strategy

        if strategy == SelectionStrategy.SEQUENTIAL:
            return list(connections)

        if strategy == SelectionStrategy.SHORTEST_FIRST:
            return self._order_shortest_first(connections)

        if strategy == SelectionStrategy.RANDOM:
            shuffled = list(connections)
            random.shuffle(shuffled)
            return shuffled

        if strategy == SelectionStrategy.PRIORITIZED:
            return self._order_by_priority(connections)

        return list(connections)

    def _order_shortest_first(self, connections: list[Connection]) -> list[Connection]:
        """Sort by Manhattan distance, power nets last."""
        def key(conn: Connection) -> tuple[int, int]:
            net_code, src, tgt = conn
            net = self.board.nets.get(net_code)
            is_power = 1 if (net and net.is_power()) else 0
            dist = abs(src.position.x - tgt.position.x) + abs(src.position.y - tgt.position.y)
            return (is_power, dist)

        return sorted(connections, key=key)

    def _order_by_priority(self, connections: list[Connection]) -> list[Connection]:
        """Sort using net priority scores."""
        from kicad_autorouter.board.net_operations import compute_net_priorities

        priorities = compute_net_priorities(self.board)
        priority_map = {p.net_code: p.priority for p in priorities}

        def key(conn: Connection) -> int:
            return priority_map.get(conn[0], 1000)

        return sorted(connections, key=key)
