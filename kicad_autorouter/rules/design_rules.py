"""
Design rules aggregation.

Collects all routing constraints: clearances, minimum widths, via sizes,
and layer-specific rules into a single queryable interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kicad_autorouter.board.net import NetClass
from kicad_autorouter.rules.clearance import ClearanceMatrix


@dataclass
class DesignRules:
    """Aggregated design rules for the autorouter.

    Provides a unified interface for querying minimum clearances,
    track widths, via dimensions, and other constraints that the
    router must satisfy.
    """

    clearance_matrix: ClearanceMatrix = field(default_factory=ClearanceMatrix)

    # Global minimums (nanometers)
    min_trace_width: int = 150_000       # 0.15mm
    min_clearance: int = 150_000         # 0.15mm
    min_via_diameter: int = 500_000      # 0.5mm
    min_via_drill: int = 200_000         # 0.2mm
    min_uvia_diameter: int = 200_000     # 0.2mm micro-via
    min_uvia_drill: int = 100_000        # 0.1mm micro-via drill

    # Board-level constraints
    board_clearance: int = 250_000       # Clearance to board edge
    allow_blind_vias: bool = False
    allow_buried_vias: bool = False
    allow_micro_vias: bool = False

    # Routing preferences
    prefer_45_degree: bool = True        # Prefer 45-degree routing
    allow_any_angle: bool = False        # Allow free-angle routing

    def get_trace_width(self, net_class: NetClass) -> int:
        """Get effective trace width for a net class."""
        return max(net_class.track_width, self.min_trace_width)

    def get_clearance(self, net_class: NetClass) -> int:
        """Get effective clearance for a net class."""
        return max(net_class.clearance, self.min_clearance)

    def get_via_diameter(self, net_class: NetClass) -> int:
        return max(net_class.via_diameter, self.min_via_diameter)

    def get_via_drill(self, net_class: NetClass) -> int:
        return max(net_class.via_drill, self.min_via_drill)

    def get_total_via_cost(self, net_class: NetClass) -> int:
        """Via shape radius including clearance (for routing cost estimation)."""
        return self.get_via_diameter(net_class) // 2 + self.get_clearance(net_class)

    def get_trace_cost_width(self, net_class: NetClass) -> int:
        """Trace half-width including clearance (for routing cost estimation)."""
        return self.get_trace_width(net_class) // 2 + self.get_clearance(net_class)

    def select_via_type(
        self,
        start_layer: int,
        end_layer: int,
        total_layers: int,
        net_class: NetClass,
    ) -> tuple[int, int, bool]:
        """Select the best via type and dimensions for a layer transition.

        Returns (diameter, drill, is_micro) based on design rules and the
        specific layers being connected.
        """
        # Through via: spans all layers
        if start_layer == 0 and end_layer == total_layers - 1:
            return (self.get_via_diameter(net_class),
                    self.get_via_drill(net_class), False)

        # Micro via: single-layer span (adjacent layers)
        if self.allow_micro_vias and abs(end_layer - start_layer) == 1:
            outer = start_layer == 0 or end_layer == total_layers - 1
            if outer:
                return (max(net_class.uvia_diameter, self.min_uvia_diameter),
                        max(net_class.uvia_drill, self.min_uvia_drill), True)

        # Blind via: touches an outer layer but doesn't span all
        if self.allow_blind_vias:
            touches_outer = start_layer == 0 or end_layer == total_layers - 1
            if touches_outer:
                return (self.get_via_diameter(net_class),
                        self.get_via_drill(net_class), False)

        # Buried via: inner layers only
        if self.allow_buried_vias:
            return (self.get_via_diameter(net_class),
                    self.get_via_drill(net_class), False)

        # Default: through via (safest)
        return (self.get_via_diameter(net_class),
                self.get_via_drill(net_class), False)
