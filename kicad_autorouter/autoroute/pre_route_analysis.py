"""Pre-routing analysis — validates board readiness before autorouting."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kicad_autorouter.board.board import RoutingBoard
    from kicad_autorouter.rules.design_rules import DesignRules


class IssueSeverity(Enum):
    """Severity levels for pre-routing analysis issues."""
    ERROR = auto()      # Will prevent successful routing
    WARNING = auto()    # May cause problems
    INFO = auto()       # Informational


@dataclass(frozen=True)
class AnalysisIssue:
    """Single issue found during pre-routing analysis."""
    severity: IssueSeverity
    category: str       # e.g. "Design Rules", "Layer Setup", "Connectivity"
    message: str
    detail: str = ""    # Optional longer explanation


@dataclass
class PreRouteReport:
    """Summary of board readiness for autorouting."""
    issues: list[AnalysisIssue] = field(default_factory=list)

    # Statistics
    total_nets: int = 0
    total_pads: int = 0
    total_connections: int = 0    # Unrouted pad pairs
    prerouted_traces: int = 0
    prerouted_vias: int = 0
    copper_layers: int = 0
    components: int = 0

    # Net class summary
    net_classes_used: list[str] = field(default_factory=list)

    # Differential pair summary
    diff_pairs_detected: int = 0

    @property
    def errors(self) -> list[AnalysisIssue]:
        """Return all ERROR-severity issues."""
        return [i for i in self.issues if i.severity == IssueSeverity.ERROR]

    @property
    def warnings(self) -> list[AnalysisIssue]:
        """Return all WARNING-severity issues."""
        return [i for i in self.issues if i.severity == IssueSeverity.WARNING]

    @property
    def infos(self) -> list[AnalysisIssue]:
        """Return all INFO-severity issues."""
        return [i for i in self.issues if i.severity == IssueSeverity.INFO]

    @property
    def ready_to_route(self) -> bool:
        """True if no errors found."""
        return len(self.errors) == 0

    def format_text(self) -> str:
        """Format report as human-readable text."""
        lines = []
        lines.append("=" * 60)
        lines.append("PRE-ROUTING ANALYSIS REPORT")
        lines.append("=" * 60)
        lines.append("")

        # Board summary
        lines.append("Board Summary:")
        lines.append(f"  Copper layers:      {self.copper_layers}")
        lines.append(f"  Components:         {self.components}")
        lines.append(f"  Nets:               {self.total_nets}")
        lines.append(f"  Pads:               {self.total_pads}")
        lines.append(f"  Connections to route: {self.total_connections}")
        lines.append(f"  Pre-routed traces:  {self.prerouted_traces}")
        lines.append(f"  Pre-routed vias:    {self.prerouted_vias}")
        if self.diff_pairs_detected > 0:
            lines.append(f"  Differential pairs: {self.diff_pairs_detected}")
        if self.net_classes_used:
            lines.append(f"  Net classes:        {', '.join(self.net_classes_used)}")
        lines.append("")

        # Issues
        if not self.issues:
            lines.append("No issues found. Board is ready to route.")
        else:
            error_count = len(self.errors)
            warn_count = len(self.warnings)
            info_count = len(self.infos)
            lines.append(f"Issues: {error_count} error(s), {warn_count} warning(s), {info_count} info")
            lines.append("")

            for sev_name, sev_issues in [("ERRORS", self.errors), ("WARNINGS", self.warnings), ("INFO", self.infos)]:
                if sev_issues:
                    lines.append(f"--- {sev_name} ---")
                    for issue in sev_issues:
                        lines.append(f"  [{issue.category}] {issue.message}")
                        if issue.detail:
                            lines.append(f"    {issue.detail}")
                    lines.append("")

        # Verdict
        lines.append("-" * 60)
        if self.ready_to_route:
            lines.append("READY TO ROUTE")
        else:
            lines.append("NOT READY — resolve errors before routing")
        lines.append("")

        return "\n".join(lines)


class PreRouteAnalyzer:
    """Analyzes board state before autorouting to catch issues early.

    Inspired by Altium Situs's pre-routing report, this checks:
    - Board geometry and layer setup
    - Design rule consistency
    - Connectivity and pad coverage
    - Component placement validity
    - Net class configuration
    - Differential pair setup
    - Overall routing feasibility
    """

    # Constants (in nanometers)
    MIN_REASONABLE_CLEARANCE = 100_000      # 0.1mm
    MIN_REASONABLE_TRACE_WIDTH = 100_000    # 0.1mm
    MIN_REASONABLE_BOARD_AREA = 1_000_000_000  # 1mm²

    def __init__(self, board: RoutingBoard, rules: DesignRules | None = None):
        """Initialize analyzer with board and optional design rules.

        Args:
            board: RoutingBoard to analyze
            rules: DesignRules to use; defaults to board.design_rules
        """
        self.board = board
        self.rules = rules or board.design_rules

    def analyze(self) -> PreRouteReport:
        """Run full pre-routing analysis and return report.

        Returns:
            PreRouteReport with all findings and statistics.
        """
        report = PreRouteReport()

        self._collect_statistics(report)
        self._check_board_basics(report)
        self._check_layer_setup(report)
        self._check_design_rules(report)
        self._check_connectivity(report)
        self._check_component_placement(report)
        self._check_net_classes(report)
        self._check_differential_pairs(report)
        self._check_routing_feasibility(report)

        return report

    def _collect_statistics(self, report: PreRouteReport) -> None:
        """Populate report with board statistics.

        Collects:
        - Net and pad counts
        - Unrouted connection count
        - Pre-routed trace and via counts
        - Copper layer count
        - Component count
        - Net classes used
        - Differential pair count (preliminary)
        """
        # Count nets and pads
        report.total_nets = len(self.board.nets)
        pads = self.board.get_pads()
        report.total_pads = len(pads)

        # Count unrouted connections (pad pairs per net)
        total_connections = 0
        for net_code, net in self.board.nets.items():
            unconnected = self.board.get_unconnected_pad_pairs(net_code)
            total_connections += len(unconnected)
        report.total_connections = total_connections

        # Count pre-routed traces and vias
        report.prerouted_traces = len(self.board.get_traces())
        report.prerouted_vias = len(self.board.get_vias())

        # Count copper layers
        copper_layers = 0
        if self.board.layer_structure and self.board.layer_structure.layers:
            from kicad_autorouter.board.layer import LayerType
            for layer in self.board.layer_structure.layers:
                if layer.layer_type == LayerType.SIGNAL:
                    copper_layers += 1
        report.copper_layers = copper_layers

        # Count components
        report.components = len(self.board.components)

        # Collect net classes used
        net_classes_used = set()
        for net in self.board.nets.values():
            net_classes_used.add(net.net_class_name)
        report.net_classes_used = sorted(net_classes_used)

        # Count differential pairs (preliminary)
        report.diff_pairs_detected = self._count_differential_pairs()

    def _check_board_basics(self, report: PreRouteReport) -> None:
        """Validate basic board geometry.

        Checks:
        - Bounding box exists and is non-zero
        - At least one pad exists
        - At least one net exists
        - Board area is reasonable relative to pad count
        """
        bbox = self.board.bounding_box

        # Check bounding box
        if bbox is None:
            report.issues.append(AnalysisIssue(
                IssueSeverity.ERROR,
                "Board Geometry",
                "No bounding box found",
                "Board geometry not properly initialized"
            ))
            return

        if bbox.x_min >= bbox.x_max or bbox.y_min >= bbox.y_max:
            report.issues.append(AnalysisIssue(
                IssueSeverity.ERROR,
                "Board Geometry",
                "Bounding box is zero-size or invalid",
                f"Box: ({bbox.x_min}, {bbox.y_min}) to ({bbox.x_max}, {bbox.y_max})"
            ))
            return

        # Check for pads
        if report.total_pads == 0:
            report.issues.append(AnalysisIssue(
                IssueSeverity.ERROR,
                "Board Geometry",
                "No pads found on board",
                "Board must have at least one pad to route"
            ))

        # Check for nets
        if report.total_nets == 0:
            report.issues.append(AnalysisIssue(
                IssueSeverity.WARNING,
                "Connectivity",
                "No nets found on board",
                "Board should have nets to route"
            ))

        # Check board area vs pad count
        board_width = bbox.x_max - bbox.x_min
        board_height = bbox.y_max - bbox.y_min
        board_area = board_width * board_height

        if board_area < self.MIN_REASONABLE_BOARD_AREA and report.total_pads > 5:
            report.issues.append(AnalysisIssue(
                IssueSeverity.WARNING,
                "Board Geometry",
                "Board area very small relative to pad count",
                f"Area: {board_area}nm² with {report.total_pads} pads; high density"
            ))

    def _check_layer_setup(self, report: PreRouteReport) -> None:
        """Validate layer structure configuration.

        Checks:
        - Single copper layer with multi-layer component pads
        - Via compatibility with design rules
        - Sensible layer count
        """
        if not self.board.layer_structure or not self.board.layer_structure.layers:
            report.issues.append(AnalysisIssue(
                IssueSeverity.ERROR,
                "Layer Setup",
                "No layer structure defined",
                "Board must define copper layers"
            ))
            return

        # INFO: Report copper layer count
        report.issues.append(AnalysisIssue(
            IssueSeverity.INFO,
            "Layer Setup",
            f"Board has {report.copper_layers} copper layer(s)"
        ))

        # Check single-layer board with multi-layer pads
        if report.copper_layers == 1:
            pads = self.board.get_pads()
            multi_layer_pads = [p for p in pads if len(p.layer_indices) > 1]
            if multi_layer_pads:
                report.issues.append(AnalysisIssue(
                    IssueSeverity.WARNING,
                    "Layer Setup",
                    "Single copper layer board with multi-layer pads",
                    f"Found {len(multi_layer_pads)} pads spanning multiple layers; "
                    "vias will be required but only 1 copper layer available"
                ))

        # Check via compatibility with design rules
        blind_buried_used = False
        pads = self.board.get_pads()
        for pad in pads:
            if len(pad.layer_indices) > 0 and len(pad.layer_indices) < report.copper_layers:
                blind_buried_used = True
                break

        if blind_buried_used:
            if not self.rules.allow_blind_vias and not self.rules.allow_buried_vias:
                report.issues.append(AnalysisIssue(
                    IssueSeverity.WARNING,
                    "Layer Setup",
                    "Blind/buried vias used but not enabled in design rules",
                    "Either enable via types or modify component placement"
                ))

    def _check_design_rules(self, report: PreRouteReport) -> None:
        """Validate design rule parameters.

        Checks:
        - Clearance values are reasonable
        - Trace widths are reasonable
        - Via geometry is physically possible
        - Net class rules comply with global rules
        """
        # Check global rule values
        if self.rules.min_clearance < self.MIN_REASONABLE_CLEARANCE:
            report.issues.append(AnalysisIssue(
                IssueSeverity.WARNING,
                "Design Rules",
                f"Minimum clearance very tight: {self.rules.min_clearance}nm (< 0.1mm)",
                "May be difficult to achieve with some routing patterns"
            ))

        if self.rules.min_trace_width < self.MIN_REASONABLE_TRACE_WIDTH:
            report.issues.append(AnalysisIssue(
                IssueSeverity.WARNING,
                "Design Rules",
                f"Minimum trace width very narrow: {self.rules.min_trace_width}nm (< 0.1mm)",
                "May limit routing feasibility, especially for dense designs"
            ))

        # Check via geometry
        if self.rules.min_via_drill > self.rules.min_via_diameter:
            report.issues.append(AnalysisIssue(
                IssueSeverity.ERROR,
                "Design Rules",
                "Via drill size larger than via diameter",
                f"Drill: {self.rules.min_via_drill}nm > Diameter: {self.rules.min_via_diameter}nm (impossible)"
            ))

        # Check net class compliance
        for net_class_name, net_class in self.board.net_classes.items():
            if net_class.clearance < self.rules.min_clearance:
                report.issues.append(AnalysisIssue(
                    IssueSeverity.ERROR,
                    "Design Rules",
                    f"Net class '{net_class_name}' clearance below minimum",
                    f"Class: {net_class.clearance}nm < Global min: {self.rules.min_clearance}nm"
                ))

            if net_class.track_width < self.rules.min_trace_width:
                report.issues.append(AnalysisIssue(
                    IssueSeverity.ERROR,
                    "Design Rules",
                    f"Net class '{net_class_name}' track width below minimum",
                    f"Class: {net_class.track_width}nm < Global min: {self.rules.min_trace_width}nm"
                ))

    def _check_connectivity(self, report: PreRouteReport) -> None:
        """Validate net connectivity and pad coverage.

        Checks:
        - Nets with pads on only one layer
        - Dangling nets (single pad)
        - Reports total connections to route
        """
        # Check each net for connectivity issues
        for net_code, net in self.board.nets.items():
            # Get pads for this net
            pads = [p for p in self.board.get_pads() if net_code in p.net_codes]

            if len(pads) == 1:
                # Dangling net: only one pad
                report.issues.append(AnalysisIssue(
                    IssueSeverity.WARNING,
                    "Connectivity",
                    f"Net '{net.name}' has only one pad (dangling)",
                    f"Single pad cannot be routed; check component placement"
                ))

            # Check if pads span multiple layers with no via path
            if len(pads) > 1:
                layer_sets = [set(p.layer_indices) for p in pads]
                if report.copper_layers > 1:
                    # Multi-layer board: pads on different layers is OK (vias handle it)
                    pass
                else:
                    # Single-layer board: pads must be on the same layer
                    all_layers = set()
                    for ls in layer_sets:
                        all_layers.update(ls)
                    if len(all_layers) > 1:
                        report.issues.append(AnalysisIssue(
                            IssueSeverity.ERROR,
                            "Connectivity",
                            f"Net '{net.name}' pads on multiple layers with single copper layer",
                            "Cannot connect pads on different layers without vias"
                        ))

        # INFO: Report total connections
        report.issues.append(AnalysisIssue(
            IssueSeverity.INFO,
            "Connectivity",
            f"Total unrouted connections: {report.total_connections}",
            f"{report.total_connections} pad pairs need routing"
        ))

    def _check_component_placement(self, report: PreRouteReport) -> None:
        """Validate component placement for routing feasibility.

        Checks:
        - Component pad overlap (pads at same position on same layer)
        - Pads outside board bounding box
        """
        bbox = self.board.bounding_box
        if bbox is None:
            return

        # Check for pad overlap
        pads = self.board.get_pads()
        pad_positions = {}  # (x, y, layer) -> [pads]

        for pad in pads:
            for layer_idx in pad.layer_indices:
                key = (pad.position.x, pad.position.y, layer_idx)
                if key not in pad_positions:
                    pad_positions[key] = []
                pad_positions[key].append(pad)

        # Report overlaps (same position, same layer, different components)
        for (x, y, layer), pads_at_pos in pad_positions.items():
            if len(pads_at_pos) > 1:
                component_ids = set(p.component_id for p in pads_at_pos)
                if len(component_ids) > 1:
                    report.issues.append(AnalysisIssue(
                        IssueSeverity.WARNING,
                        "Component Placement",
                        f"Pad overlap at position ({x}, {y}) on layer {layer}",
                        f"Pads from {len(component_ids)} different components at same location"
                    ))

        # Check for pads outside board
        outside_pads = []
        for pad in pads:
            if (pad.position.x < bbox.x_min or pad.position.x > bbox.x_max or
                pad.position.y < bbox.y_min or pad.position.y > bbox.y_max):
                outside_pads.append(pad)

        if outside_pads:
            report.issues.append(AnalysisIssue(
                IssueSeverity.WARNING,
                "Component Placement",
                f"{len(outside_pads)} pad(s) outside board bounding box",
                f"These pads cannot be routed properly"
            ))

    def _check_net_classes(self, report: PreRouteReport) -> None:
        """Validate net class configuration.

        Checks:
        - All referenced net classes exist in board
        - Reports net class parameters
        """
        # Check that all nets reference valid net classes
        for net_code, net in self.board.nets.items():
            if net.net_class_name not in self.board.net_classes:
                report.issues.append(AnalysisIssue(
                    IssueSeverity.WARNING,
                    "Net Classes",
                    f"Net '{net.name}' references undefined net class '{net.net_class_name}'",
                    "Check net class configuration"
                ))

        # INFO: Report net class parameters
        if self.board.net_classes:
            details = []
            for nc_name in sorted(self.board.net_classes.keys()):
                nc = self.board.net_classes[nc_name]
                details.append(
                    f"  {nc_name}: width={nc.track_width}nm, "
                    f"clear={nc.clearance}nm, "
                    f"via={nc.via_diameter}nm"
                )

            report.issues.append(AnalysisIssue(
                IssueSeverity.INFO,
                "Net Classes",
                f"Net classes configured ({len(self.board.net_classes)} total)",
                "\n".join(details)
            ))

    def _check_differential_pairs(self, report: PreRouteReport) -> None:
        """Detect and validate differential pair configuration.

        Checks:
        - Identifies diff pairs by naming convention (P/N, +/-, _P/_N)
        - Warns if pair nets use different net classes
        """
        diff_pairs = self._find_differential_pairs()

        if diff_pairs:
            report.issues.append(AnalysisIssue(
                IssueSeverity.INFO,
                "Differential Pairs",
                f"Detected {len(diff_pairs)} differential pair(s)",
                f"Pairs: {', '.join(f'{p[0].name}/{p[1].name}' for p in diff_pairs)}"
            ))

            # Check net class consistency
            for net_p, net_n in diff_pairs:
                if net_p.net_class_name != net_n.net_class_name:
                    report.issues.append(AnalysisIssue(
                        IssueSeverity.WARNING,
                        "Differential Pairs",
                        f"Diff pair '{net_p.name}/{net_n.name}' uses different net classes",
                        f"P uses '{net_p.net_class_name}', N uses '{net_n.net_class_name}'"
                    ))

    def _check_routing_feasibility(self, report: PreRouteReport) -> None:
        """Assess overall routing complexity and feasibility.

        Checks:
        - Connection density (connections per unit area)
        - Average connection distance relative to board size
        - Overall routing complexity classification
        """
        bbox = self.board.bounding_box
        if bbox is None or report.total_pads == 0:
            return

        board_area = (bbox.x_max - bbox.x_min) * (bbox.y_max - bbox.y_min)

        # Calculate connection density
        if board_area > 0:
            connection_density = report.total_connections / board_area
            # Warn if > 1 connection per mm²
            if connection_density > 1e-6:  # 1e-6 = 1 / 1e6 nm²
                report.issues.append(AnalysisIssue(
                    IssueSeverity.WARNING,
                    "Routing Feasibility",
                    "Very high connection density",
                    f"{connection_density:.2e} connections/nm²; may be difficult to route"
                ))

        # Calculate average connection distance
        if report.total_connections > 0:
            board_width = bbox.x_max - bbox.x_min
            board_height = bbox.y_max - bbox.y_min
            board_diagonal = math.sqrt(board_width**2 + board_height**2)

            # Estimate: connections distributed across board
            avg_distance = board_diagonal / math.sqrt(report.total_connections) if report.total_connections > 0 else 0

            # Warn if average distance is very large (> 30% of diagonal)
            if avg_distance > 0.3 * board_diagonal:
                report.issues.append(AnalysisIssue(
                    IssueSeverity.WARNING,
                    "Routing Feasibility",
                    "Long average connection distance",
                    f"Est. {avg_distance:.0f}nm avg; board diagonal {board_diagonal:.0f}nm"
                ))

        # Estimate routing complexity
        complexity = self._estimate_routing_complexity(report)
        report.issues.append(AnalysisIssue(
            IssueSeverity.INFO,
            "Routing Feasibility",
            f"Estimated routing complexity: {complexity}",
            f"Based on {report.total_nets} nets, {report.copper_layers} layers, "
            f"{report.total_connections} connections"
        ))

    # Helper methods

    def _count_differential_pairs(self) -> int:
        """Count detected differential pairs."""
        return len(self._find_differential_pairs())

    def _find_differential_pairs(self) -> list[tuple]:
        """Find differential pairs by naming convention.

        Looks for patterns:
        - SIG_P / SIG_N
        - SIG_+ / SIG_-
        - SIGP / SIGN (with P/N suffix)

        Returns:
            List of (net_p, net_n) tuples
        """
        pairs = []
        nets_by_name = {net.name: net for net in self.board.nets.values()}
        matched = set()

        for net_name, net in nets_by_name.items():
            if net_name in matched:
                continue

            # Try to find matching pair
            for pattern_p, pattern_n in [
                ("_P", "_N"), ("_p", "_n"),
                ("+", "-"),
                ("P", "N"),  # Suffix match
            ]:
                if net_name.endswith(pattern_p):
                    base = net_name[:-len(pattern_p)]
                    partner_name = base + pattern_n
                    if partner_name in nets_by_name:
                        pairs.append((net, nets_by_name[partner_name]))
                        matched.add(net_name)
                        matched.add(partner_name)
                        break

        return pairs

    def _estimate_routing_complexity(self, report: PreRouteReport) -> str:
        """Estimate overall routing complexity.

        Returns:
            "simple", "moderate", or "complex"
        """
        # Heuristic: based on net count, layer count, connection density
        score = 0

        # Net count factor
        if report.total_nets > 100:
            score += 2
        elif report.total_nets > 50:
            score += 1

        # Layer count factor
        if report.copper_layers <= 2:
            score += 2
        elif report.copper_layers <= 4:
            score += 1

        # Connection density
        bbox = self.board.bounding_box
        if bbox:
            board_area = (bbox.x_max - bbox.x_min) * (bbox.y_max - bbox.y_min)
            if board_area > 0:
                density = report.total_connections / board_area
                if density > 1e-6:
                    score += 2
                elif density > 5e-7:
                    score += 1

        if score >= 4:
            return "complex"
        elif score >= 2:
            return "moderate"
        else:
            return "simple"
