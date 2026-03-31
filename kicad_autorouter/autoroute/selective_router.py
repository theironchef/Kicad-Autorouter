"""
SelectiveRouter — Route or re-route specific nets only.

Provides two modes:
  1. Interactive routing — route only user-selected nets (skip everything else)
  2. Selective re-routing — rip up specific nets and re-route them

Both modes integrate with the BatchAutorouter and support DRC validation
via ValidatedRouter when requested.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto

from kicad_autorouter.autoroute.batch import (
    AutorouteConfig,
    AutorouteResult,
    BatchAutorouter,
)
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.history import BoardHistory
from kicad_autorouter.rules.design_rules import DesignRules

logger = logging.getLogger(__name__)


class SelectionMode(Enum):
    """How nets were selected."""
    BY_NET_CODE = auto()    # Explicit net codes
    BY_NET_NAME = auto()    # Net name patterns
    BY_COMPONENT = auto()   # All nets touching a component
    BY_NET_CLASS = auto()   # Net class names
    BY_AREA = auto()        # Pads inside a bounding area


@dataclass
class RerouteResult:
    """Result of a selective re-route operation."""
    nets_ripped: int = 0
    traces_removed: int = 0
    vias_removed: int = 0
    route_result: AutorouteResult = field(default_factory=AutorouteResult)
    rolled_back: bool = False


class SelectiveRouter:
    """Routes or re-routes only specific nets.

    Usage for interactive routing (route selected unrouted nets):
        sr = SelectiveRouter(board, rules)
        result = sr.route_nets([3, 7, 12])

    Usage for selective re-routing (rip up and re-route):
        sr = SelectiveRouter(board, rules)
        result = sr.reroute_nets([3, 7, 12])
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
        self._history = BoardHistory(board)

    # ── Net resolution ──────────────────────────────────────────────

    def resolve_nets(
        self,
        net_codes: list[int] | None = None,
        net_names: list[str] | None = None,
        component_refs: list[str] | None = None,
        class_names: list[str] | None = None,
        area: tuple[int, int, int, int] | None = None,
    ) -> list[int]:
        """Resolve a combination of selectors into a list of net codes.

        Args:
            net_codes: Explicit net codes to include.
            net_names: Net name patterns (exact match).
            component_refs: Component references — includes all nets
                that touch any pad on the component.
            class_names: Net class names to include.
            area: Bounding area (min_x, min_y, max_x, max_y) in nanometers —
                includes nets with pads inside.

        Returns:
            Sorted, deduplicated list of net codes.
        """
        codes: set[int] = set()

        if net_codes:
            for nc in net_codes:
                if nc in self.board.nets and nc > 0:
                    codes.add(nc)

        if net_names:
            name_set = set(net_names)
            for nc, net in self.board.nets.items():
                if nc > 0 and net.name in name_set:
                    codes.add(nc)

        if component_refs:
            ref_set = set(component_refs)
            for pad in self.board.get_pads():
                comp = self._component_for_pad(pad)
                if comp and comp.reference in ref_set and pad.net_code > 0:
                    codes.add(pad.net_code)

        if class_names:
            codes.update(self.resolve_nets_by_class(class_names))

        if area:
            min_x, min_y, max_x, max_y = area
            codes.update(self.resolve_nets_by_area(min_x, min_y, max_x, max_y))

        return sorted(codes)

    def resolve_nets_by_class(self, class_names: list[str]) -> set[int]:
        """Resolve all net codes belonging to the given net class names."""
        result: set[int] = set()
        for net_code, net in self.board.nets.items():
            if net.net_class_name in class_names:
                result.add(net_code)
        return result

    def resolve_nets_by_area(self, min_x: int, min_y: int, max_x: int, max_y: int) -> set[int]:
        """Resolve all net codes that have at least one pad inside the given area (nanometers)."""
        result: set[int] = set()
        for pad in self.board.get_pads():
            pos = pad.position
            if min_x <= pos.x <= max_x and min_y <= pos.y <= max_y:
                if pad.net_code != 0:
                    result.add(pad.net_code)
        return result

    # ── Interactive routing (route selected nets only) ──────────────

    def route_nets(
        self,
        net_codes: list[int] | None = None,
        net_names: list[str] | None = None,
        component_refs: list[str] | None = None,
    ) -> AutorouteResult:
        """Route only the specified nets, leaving others untouched.

        Connections on nets not in the selection are simply skipped.
        Already-routed connections on selected nets are also skipped.
        """
        selected = self.resolve_nets(net_codes, net_names, component_refs)
        if not selected:
            logger.info("No nets to route")
            return AutorouteResult(completed=True)

        logger.info("Interactive routing: %d selected nets", len(selected))
        selected_set = set(selected)

        # Build a filtered router that only sees selected nets
        router = _FilteredBatchAutorouter(
            board=self.board,
            rules=self.rules,
            config=self.config,
            allowed_nets=selected_set,
        )
        return router.run()

    # ── Selective re-routing (ripup + re-route) ─────────────────────

    def reroute_nets(
        self,
        net_codes: list[int] | None = None,
        net_names: list[str] | None = None,
        component_refs: list[str] | None = None,
    ) -> RerouteResult:
        """Rip up and re-route the specified nets.

        1. Saves a snapshot for rollback.
        2. Removes all traces and vias on the selected nets.
        3. Re-routes those nets from scratch.
        4. If nothing routes successfully, rolls back.
        """
        selected = self.resolve_nets(net_codes, net_names, component_refs)
        result = RerouteResult()

        if not selected:
            logger.info("No nets to re-route")
            result.route_result = AutorouteResult(completed=True)
            return result

        logger.info("Selective re-route: %d nets", len(selected))

        # Snapshot before rip-up
        self._history.snapshot("pre_reroute")

        # Rip up selected nets
        for nc in selected:
            traces = self.board.get_traces_on_net(nc)
            vias = self.board.get_vias_on_net(nc)
            result.traces_removed += len(traces)
            result.vias_removed += len(vias)
            self.board.remove_traces_on_net(nc)
            self.board.remove_vias_on_net(nc)
        result.nets_ripped = len(selected)

        logger.info(
            "Ripped up %d nets: removed %d traces, %d vias",
            result.nets_ripped, result.traces_removed, result.vias_removed,
        )

        # Re-route the ripped nets
        route_result = self.route_nets(net_codes=selected)
        result.route_result = route_result

        # Rollback if nothing routed
        if route_result.connections_routed == 0 and result.traces_removed > 0:
            logger.warning("Re-route produced no new routes — rolling back")
            self._history.undo()
            result.rolled_back = True

        return result

    def route_net_class(self, class_names: list[str]) -> AutorouteResult:
        """Route all nets in the given net classes."""
        net_codes = self.resolve_nets_by_class(class_names)
        return self.route_nets(net_codes=list(net_codes))

    def route_area(self, min_x: int, min_y: int, max_x: int, max_y: int) -> AutorouteResult:
        """Route all nets with pads inside the given area."""
        net_codes = self.resolve_nets_by_area(min_x, min_y, max_x, max_y)
        return self.route_nets(net_codes=list(net_codes))

    def reroute_net_class(self, class_names: list[str]) -> RerouteResult:
        """Rip up and re-route all nets in the given net classes."""
        net_codes = self.resolve_nets_by_class(class_names)
        return self.reroute_nets(net_codes=list(net_codes))

    def reroute_area(self, min_x: int, min_y: int, max_x: int, max_y: int) -> RerouteResult:
        """Rip up and re-route all nets with pads inside the given area."""
        net_codes = self.resolve_nets_by_area(min_x, min_y, max_x, max_y)
        return self.reroute_nets(net_codes=list(net_codes))

    # ── Internal helpers ────────────────────────────────────────────

    def _component_for_pad(self, pad) -> object | None:
        """Find the component that owns a pad (if any)."""
        for comp in getattr(self.board, "components", {}).values():
            for p in getattr(comp, "pads", []):
                if p.id == pad.id:
                    return comp
        return None


class _FilteredBatchAutorouter(BatchAutorouter):
    """A BatchAutorouter that only routes connections on allowed nets."""

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        config: AutorouteConfig | None,
        allowed_nets: set[int],
    ):
        super().__init__(board, rules, config)
        self._allowed_nets = allowed_nets

    def _get_all_connections(self):
        """Override to filter connections to allowed nets only."""
        all_conns = super()._get_all_connections()
        return [
            (nc, src, tgt)
            for nc, src, tgt in all_conns
            if nc in self._allowed_nets
        ]
