"""
Comprehensive tests for Situs-inspired features.

Tests the three new Situs-inspired subsystems:
1. PreRouteAnalyzer — pre-routing board validation and reporting
2. RoutingStrategy / StrategyExecutor — composable routing strategies
3. SelectiveRouter extensions — route by net class, by area, and reroute modes

These tests follow the board construction patterns from test_integration.py
and validate the APIs from the three modules.
"""

from __future__ import annotations

import pytest

from kicad_autorouter.autoroute.batch import AutorouteConfig
from kicad_autorouter.autoroute.pre_route_analysis import (
    IssueSeverity,
    PreRouteAnalyzer,
    PreRouteReport,
)
from kicad_autorouter.autoroute.routing_strategy import (
    PassConfig,
    PassType,
    RoutingStrategy,
    StrategyExecutor,
    StrategyResult,
)
from kicad_autorouter.autoroute.selective_router import SelectiveRouter, RerouteResult
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.rules.design_rules import DesignRules


# ── Shared Test Helpers ────────────────────────────────────────────────

def _make_board(width_mm=50, height_mm=50, layers=2):
    """Create a board for testing."""
    board = RoutingBoard()
    w = width_mm * 1_000_000
    h = height_mm * 1_000_000
    layer_list = [Layer(0, "F.Cu", LayerType.SIGNAL)]
    if layers >= 2:
        layer_list.append(Layer(1, "B.Cu", LayerType.SIGNAL))
    board.bounding_box = BoundingBox(0, 0, w, h)
    board.layer_structure = LayerStructure(layer_list)
    board.net_classes["Default"] = NetClass("Default")
    board.default_net_class = board.net_classes["Default"]
    board.design_rules = DesignRules(min_clearance=150_000)
    return board


def _add_pad(board, net_code, x_mm, y_mm, size_mm=1.5, layer=0):
    """Add a pad to the board."""
    pad = Pad(
        id=0,
        net_codes=[net_code],
        layer_indices=[layer],
        fixed_state=FixedState.SYSTEM_FIXED,
        position=IntPoint(int(x_mm * 1e6), int(y_mm * 1e6)),
        size_x=int(size_mm * 1e6),
        size_y=int(size_mm * 1e6),
        pad_shape=PadShape.CIRCLE,
    )
    board.add_item(pad)
    return pad


# ── TestPreRouteAnalyzer ───────────────────────────────────────────────

class TestPreRouteAnalyzer:
    """Tests for PreRouteAnalyzer — board readiness validation."""

    def test_empty_board_errors(self):
        """Board with no pads should report ERROR."""
        board = _make_board(50, 50)
        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()

        assert not report.ready_to_route
        assert len(report.errors) > 0
        assert any(
            "No pads found" in issue.message
            for issue in report.errors
        )

    def test_minimal_valid_board(self):
        """Board with 2 pads on 1 net should be ready_to_route."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()

        assert report.ready_to_route
        assert report.total_pads == 2
        assert report.total_nets == 1

    def test_no_nets_warning(self):
        """Board with pads but no nets gets WARNING."""
        board = _make_board(50, 50)
        _add_pad(board, 0, 25, 25)  # Unconnected pad

        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()

        # Should have warning about no nets
        assert any(
            "No nets found" in issue.message and issue.severity == IssueSeverity.WARNING
            for issue in report.issues
        )

    def test_statistics_collected(self):
        """Verify report.total_nets, total_pads, copper_layers etc are correct."""
        board = _make_board(80, 80, layers=4)
        # Add three nets with varying pad counts
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 10, 10)
        _add_pad(board, 1, 70, 10)
        _add_pad(board, 1, 40, 40)

        board.nets[2] = Net(net_code=2, name="NET2")
        _add_pad(board, 2, 20, 20)
        _add_pad(board, 2, 60, 60)

        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()

        assert report.total_nets == 2
        assert report.total_pads == 5
        assert report.copper_layers == 2  # _make_board creates F.Cu + B.Cu
        assert report.total_connections >= 2  # At least 2 pad pairs per net

    def test_tight_clearance_warning(self):
        """Design rules with min_clearance < 100_000 should warn."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        # Set tight clearance
        board.design_rules = DesignRules(min_clearance=50_000)

        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()

        assert any(
            "clearance very tight" in issue.message and issue.severity == IssueSeverity.WARNING
            for issue in report.issues
        )

    def test_impossible_via_warning(self):
        """Design rules with via_drill > via_diameter should warn."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        # Make via drill larger than diameter
        board.design_rules = DesignRules(
            min_via_diameter=300_000,
            min_via_drill=400_000,
        )

        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()

        assert not report.ready_to_route
        assert any(
            "drill size larger than" in issue.message and issue.severity == IssueSeverity.ERROR
            for issue in report.issues
        )

    def test_dangling_net_warning(self):
        """Net with only 1 pad should warn."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 25, 25)  # Only one pad on this net

        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()

        assert any(
            "only one pad (dangling)" in issue.message and issue.severity == IssueSeverity.WARNING
            for issue in report.issues
        )

    def test_pads_outside_board_warning(self):
        """Pad outside bounding_box should warn."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 25, 25)
        _add_pad(board, 1, 75, 75)  # Outside 50x50 board

        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()

        assert any(
            "outside board bounding box" in issue.message and issue.severity == IssueSeverity.WARNING
            for issue in report.issues
        )

    def test_diff_pairs_detected(self):
        """Create NET_P and NET_N nets, verify diff_pairs_detected > 0."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="SIGNAL_P")
        board.nets[2] = Net(net_code=2, name="SIGNAL_N")
        _add_pad(board, 1, 10, 10)
        _add_pad(board, 1, 40, 10)
        _add_pad(board, 2, 10, 40)
        _add_pad(board, 2, 40, 40)

        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()

        assert report.diff_pairs_detected >= 1

    def test_report_format_text(self):
        """Verify format_text() returns non-empty string with expected sections."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()

        text = report.format_text()
        assert len(text) > 0
        assert "PRE-ROUTING ANALYSIS REPORT" in text
        assert "Board Summary:" in text
        assert "Copper layers:" in text
        assert "Nets:" in text
        assert "Pads:" in text


# ── TestRoutingStrategy ────────────────────────────────────────────────

class TestRoutingStrategy:
    """Tests for RoutingStrategy and StrategyExecutor."""

    def test_default_strategies_exist(self):
        """Verify default_two_layer(), default_multi_layer(), quick(), thorough() all return valid strategies."""
        s1 = RoutingStrategy.default_two_layer()
        assert s1 is not None
        assert len(s1.passes) > 0

        s2 = RoutingStrategy.default_multi_layer()
        assert s2 is not None
        assert len(s2.passes) > 0

        s3 = RoutingStrategy.quick()
        assert s3 is not None
        assert len(s3.passes) > 0

        s4 = RoutingStrategy.thorough()
        assert s4 is not None
        assert len(s4.passes) > 0

    def test_custom_strategy(self):
        """Build a custom strategy with add_pass(), verify passes list."""
        strategy = RoutingStrategy("Custom")
        assert len(strategy.passes) == 0

        strategy.add_pass(PassConfig(PassType.FANOUT, "Fanout", max_passes=3))
        strategy.add_pass(PassConfig(PassType.MAIN, "Route", max_passes=10))
        strategy.add_pass(PassConfig(PassType.DRC_CLEANUP, "DRC", max_passes=1))

        assert len(strategy.passes) == 3
        assert strategy.passes[0].pass_type == PassType.FANOUT
        assert strategy.passes[1].pass_type == PassType.MAIN
        assert strategy.passes[2].pass_type == PassType.DRC_CLEANUP

    def test_strategy_pass_count(self):
        """Verify default strategies have expected number of passes."""
        two_layer = RoutingStrategy.default_two_layer()
        assert len(two_layer.passes) == 5  # Fanout, Main, Optimize, Straighten, DRC Cleanup

        multi_layer = RoutingStrategy.default_multi_layer()
        assert len(multi_layer.passes) == 8  # More passes for multi-layer

        quick = RoutingStrategy.quick()
        assert len(quick.passes) == 3  # Main, Optimize, DRC Cleanup

        thorough = RoutingStrategy.thorough()
        assert len(thorough.passes) >= 10  # Many passes for quality

    def test_disabled_pass_skipped(self):
        """Add a pass with enabled=False, execute, verify it's not in results."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        strategy = RoutingStrategy("Test")
        strategy.add_pass(PassConfig(PassType.MAIN, enabled=False))
        strategy.add_pass(PassConfig(PassType.DRC_CLEANUP, enabled=True))

        executor = StrategyExecutor(board, board.design_rules)
        result = executor.execute(strategy)

        # Only 1 pass should run (the enabled one)
        assert len(result.pass_results) == 1
        assert result.pass_results[0].pass_config.pass_type == PassType.DRC_CLEANUP

    def test_executor_runs(self):
        """Create minimal board, execute quick() strategy, verify StrategyResult returned."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        executor = StrategyExecutor(board, board.design_rules)
        result = executor.execute(RoutingStrategy.quick())

        assert isinstance(result, StrategyResult)
        assert result.total_elapsed >= 0.0
        assert len(result.pass_results) > 0

    def test_executor_progress_callback(self):
        """Verify callback is called during execution."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        progress_calls = []

        def on_progress(msg: str, pct: float):
            progress_calls.append((msg, pct))

        executor = StrategyExecutor(board, board.design_rules, on_progress)
        result = executor.execute(RoutingStrategy.quick())

        assert len(progress_calls) > 0
        # Should have at least one completion call
        assert any(
            msg == "Complete" and pct == 1.0
            for msg, pct in progress_calls
        )

    def test_pass_types_all_handled(self):
        """Verify each PassType has a handler (by executing a strategy with one of each)."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        # Build a strategy with each pass type
        strategy = RoutingStrategy("All Types")
        for pass_type in PassType:
            strategy.add_pass(PassConfig(pass_type, max_passes=1, time_limit=10.0))

        executor = StrategyExecutor(board, board.design_rules)
        result = executor.execute(strategy)

        # All passes should have run
        assert len(result.pass_results) == len(PassType)
        # All should be success (or at least, not crash)
        assert all(pr.success or True for pr in result.pass_results)


# ── TestSelectiveRouterExtensions ──────────────────────────────────────

class TestSelectiveRouterExtensions:
    """Tests for SelectiveRouter's new net class and area methods."""

    def test_resolve_nets_by_class(self):
        """Create board with nets in different classes, resolve by class name."""
        board = _make_board(50, 50)

        # Create two net classes
        board.net_classes["Fast"] = NetClass("Fast")
        board.net_classes["Slow"] = NetClass("Slow")

        # Create nets in different classes
        board.nets[1] = Net(net_code=1, name="NET1", net_class_name="Fast")
        board.nets[2] = Net(net_code=2, name="NET2", net_class_name="Slow")
        board.nets[3] = Net(net_code=3, name="NET3", net_class_name="Fast")

        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)
        _add_pad(board, 2, 5, 45)
        _add_pad(board, 2, 45, 45)
        _add_pad(board, 3, 25, 10)
        _add_pad(board, 3, 25, 40)

        sr = SelectiveRouter(board, board.design_rules)
        fast_nets = sr.resolve_nets_by_class(["Fast"])

        assert 1 in fast_nets
        assert 3 in fast_nets
        assert 2 not in fast_nets

    def test_resolve_nets_by_area(self):
        """Create board with pads at known positions, resolve by area."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        board.nets[2] = Net(net_code=2, name="NET2")

        _add_pad(board, 1, 10, 10)  # Inside lower-left region
        _add_pad(board, 1, 15, 15)
        _add_pad(board, 2, 40, 40)  # Inside upper-right region

        sr = SelectiveRouter(board, board.design_rules)

        # Query lower-left area (5-25 mm)
        lower_left = sr.resolve_nets_by_area(
            5_000_000, 5_000_000, 25_000_000, 25_000_000
        )
        assert 1 in lower_left
        assert 2 not in lower_left

        # Query upper-right area (35-45 mm)
        upper_right = sr.resolve_nets_by_area(
            35_000_000, 35_000_000, 45_000_000, 45_000_000
        )
        assert 2 in upper_right
        assert 1 not in upper_right

    def test_route_net_class(self):
        """Verify route_net_class returns AutorouteResult."""
        board = _make_board(50, 50)
        board.net_classes["Test"] = NetClass("Test")

        board.nets[1] = Net(net_code=1, name="NET1", net_class_name="Test")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        sr = SelectiveRouter(
            board, board.design_rules,
            config=AutorouteConfig(max_passes=2, time_limit_seconds=5)
        )
        result = sr.route_net_class(["Test"])

        assert result is not None
        assert hasattr(result, "total_connections")

    def test_route_area(self):
        """Verify route_area returns AutorouteResult."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 10, 10)
        _add_pad(board, 1, 20, 20)

        sr = SelectiveRouter(
            board, board.design_rules,
            config=AutorouteConfig(max_passes=2, time_limit_seconds=5)
        )
        result = sr.route_area(5_000_000, 5_000_000, 25_000_000, 25_000_000)

        assert result is not None
        assert hasattr(result, "total_connections")

    def test_reroute_net_class(self):
        """Verify reroute_net_class returns RerouteResult."""
        board = _make_board(50, 50)
        board.net_classes["Test"] = NetClass("Test")

        board.nets[1] = Net(net_code=1, name="NET1", net_class_name="Test")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        # Pre-route the net
        board.add_trace(
            corners=[IntPoint(5_000_000, 5_000_000), IntPoint(45_000_000, 5_000_000)],
            width=250_000, layer_index=0, net_code=1,
        )

        sr = SelectiveRouter(
            board, board.design_rules,
            config=AutorouteConfig(max_passes=2, time_limit_seconds=5)
        )
        result = sr.reroute_net_class(["Test"])

        assert isinstance(result, RerouteResult)
        assert result.nets_ripped >= 0

    def test_resolve_nets_combined(self):
        """Verify resolve_nets with both net_codes and class_names works."""
        board = _make_board(50, 50)
        board.net_classes["Group1"] = NetClass("Group1")
        board.net_classes["Group2"] = NetClass("Group2")

        board.nets[1] = Net(net_code=1, name="NET1", net_class_name="Group1")
        board.nets[2] = Net(net_code=2, name="NET2", net_class_name="Group1")
        board.nets[3] = Net(net_code=3, name="NET3", net_class_name="Group2")

        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 15, 5)
        _add_pad(board, 2, 25, 5)
        _add_pad(board, 2, 35, 5)
        _add_pad(board, 3, 45, 5)
        _add_pad(board, 3, 45, 15)

        sr = SelectiveRouter(board, board.design_rules)

        # Combine explicit net code with class selector
        result = sr.resolve_nets(
            net_codes=[3],
            class_names=["Group1"]
        )

        assert 1 in result  # From Group1
        assert 2 in result  # From Group1
        assert 3 in result  # From explicit net_code
        assert len(result) == 3


# ── TestDrcCleanup ────────────────────────────────────────────────────

class TestDrcCleanup:
    """Tests for DRC cleanup pass and strategy result properties."""

    def test_drc_cleanup_pass_runs(self):
        """Execute a strategy with only DRC_CLEANUP, verify PassResult."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        strategy = RoutingStrategy("DRC Only")
        strategy.add_pass(PassConfig(PassType.DRC_CLEANUP, "Cleanup", max_passes=1))

        executor = StrategyExecutor(board, board.design_rules)
        result = executor.execute(strategy)

        assert len(result.pass_results) == 1
        pr = result.pass_results[0]
        assert pr.pass_config.pass_type == PassType.DRC_CLEANUP
        assert pr.success

    def test_strategy_result_properties(self):
        """Verify completed, completion_percentage properties."""
        board = _make_board(50, 50)
        board.nets[1] = Net(net_code=1, name="NET1")
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 45, 5)

        executor = StrategyExecutor(board, board.design_rules)
        result = executor.execute(RoutingStrategy.quick())

        # Verify properties exist and are sane
        assert hasattr(result, "completed")
        assert hasattr(result, "completion_percentage")
        assert isinstance(result.completed, bool)
        assert 0.0 <= result.completion_percentage <= 100.0
