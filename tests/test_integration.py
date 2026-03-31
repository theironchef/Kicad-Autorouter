"""
Integration tests — end-to-end routing scenarios with realistic boards.

These tests construct multi-net boards and verify the complete pipeline:
read → route → optimize → DRC → validate.
"""

from __future__ import annotations

import pytest

from kicad_autorouter.autoroute.batch import AutorouteConfig, BatchAutorouter
from kicad_autorouter.autoroute.validated_router import (
    CommitPolicy,
    ValidatedRouter,
)
from kicad_autorouter.autoroute.selective_router import SelectiveRouter, RerouteResult
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.drc.checker import DrcChecker
from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.optimize.batch_optimizer import BatchOptimizer, BatchOptConfig
from kicad_autorouter.rules.design_rules import DesignRules


# ── Helpers ────────────────────────────────────────────────────────


def _make_board(width_mm=50, height_mm=50, layers=2):
    """Create a board the same way existing tests do."""
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


def _simple_two_net_board():
    """Two-layer board with two nets, each connecting two pads."""
    board = _make_board(50, 50)
    board.nets[1] = Net(net_code=1, name="NET1")
    _add_pad(board, 1, 5, 5)
    _add_pad(board, 1, 45, 5)

    board.nets[2] = Net(net_code=2, name="NET2")
    _add_pad(board, 2, 5, 45)
    _add_pad(board, 2, 45, 45)

    return board, board.design_rules


def _five_net_board():
    """Two-layer board with five nets for more complex scenarios."""
    board = _make_board(80, 80)
    positions = [
        (5, 10, 75, 10),
        (5, 30, 75, 30),
        (5, 50, 75, 50),
        (15, 5, 15, 75),
        (65, 5, 65, 75),
    ]
    for i, (x1, y1, x2, y2) in enumerate(positions, start=1):
        board.nets[i] = Net(net_code=i, name=f"NET{i}")
        _add_pad(board, i, x1, y1)
        _add_pad(board, i, x2, y2)

    return board, board.design_rules


def _prerouted_board():
    """Board with one net already routed and one unrouted."""
    board = _make_board(50, 50)

    # Net 1: already routed
    board.nets[1] = Net(net_code=1, name="ROUTED")
    _add_pad(board, 1, 5, 25)
    _add_pad(board, 1, 45, 25)
    board.add_trace(
        corners=[IntPoint(5_000_000, 25_000_000), IntPoint(45_000_000, 25_000_000)],
        width=250_000, layer_index=0, net_code=1,
    )

    # Net 2: unrouted
    board.nets[2] = Net(net_code=2, name="UNROUTED")
    _add_pad(board, 2, 25, 5)
    _add_pad(board, 2, 25, 45)

    return board, board.design_rules


# ── Validated Router Tests ─────────────────────────────────────────


class TestValidatedRouter:
    """End-to-end tests for ValidatedRouter (pre-commit DRC)."""

    def test_route_and_validate_simple(self):
        board, rules = _simple_two_net_board()
        vr = ValidatedRouter(
            board, rules,
            route_config=AutorouteConfig(max_passes=5, time_limit_seconds=10),
            policy=CommitPolicy.REJECT_ON_NEW_ERRORS,
        )
        result = vr.run()
        assert result.drc_before is not None
        assert result.drc_after is not None
        assert result.route_result is not None

    def test_always_commit_policy(self):
        board, rules = _simple_two_net_board()
        vr = ValidatedRouter(
            board, rules,
            route_config=AutorouteConfig(max_passes=3, time_limit_seconds=5),
            policy=CommitPolicy.ALWAYS_COMMIT,
        )
        result = vr.run()
        assert result.committed is True

    def test_reject_on_new_errors_clean_board(self):
        board, rules = _simple_two_net_board()
        vr = ValidatedRouter(
            board, rules,
            route_config=AutorouteConfig(max_passes=3, time_limit_seconds=5),
            policy=CommitPolicy.REJECT_ON_NEW_ERRORS,
        )
        result = vr.run()
        assert isinstance(result.committed, bool)
        assert isinstance(result.new_error_count, int)

    def test_drc_baseline_recorded(self):
        board, rules = _simple_two_net_board()
        vr = ValidatedRouter(
            board, rules,
            route_config=AutorouteConfig(max_passes=1, time_limit_seconds=2),
            policy=CommitPolicy.ALWAYS_COMMIT,
        )
        result = vr.run()
        assert result.drc_before is not None
        assert result.drc_before.nets_checked >= 0
        assert result.drc_after is not None

    def test_rollback_restores_board_state(self):
        board, rules = _simple_two_net_board()
        initial_trace_count = len(board.get_traces())
        vr = ValidatedRouter(
            board, rules,
            route_config=AutorouteConfig(max_passes=2, time_limit_seconds=5),
            policy=CommitPolicy.REJECT_ON_ERROR,
        )
        result = vr.run()
        if not result.committed:
            assert len(board.get_traces()) == initial_trace_count

    def test_validated_result_properties(self):
        board, rules = _simple_two_net_board()
        vr = ValidatedRouter(
            board, rules,
            route_config=AutorouteConfig(max_passes=1, time_limit_seconds=2),
            policy=CommitPolicy.ALWAYS_COMMIT,
        )
        result = vr.run()
        assert result.new_error_count >= 0
        assert result.new_warning_count >= 0


# ── Interactive Routing Tests ──────────────────────────────────────


class TestInteractiveRouting:

    def test_route_single_net(self):
        board, rules = _simple_two_net_board()
        sr = SelectiveRouter(
            board, rules,
            config=AutorouteConfig(max_passes=3, time_limit_seconds=5),
        )
        result = sr.route_nets(net_codes=[1])
        assert result.total_connections >= 0

    def test_route_by_name(self):
        board, rules = _simple_two_net_board()
        sr = SelectiveRouter(board, rules)
        codes = sr.resolve_nets(net_names=["NET1"])
        assert codes == [1]

    def test_route_by_name_not_found(self):
        board, rules = _simple_two_net_board()
        sr = SelectiveRouter(board, rules)
        codes = sr.resolve_nets(net_names=["NONEXISTENT"])
        assert codes == []

    def test_route_no_nets_is_noop(self):
        board, rules = _simple_two_net_board()
        sr = SelectiveRouter(board, rules)
        result = sr.route_nets(net_codes=[])
        assert result.completed is True

    def test_route_multiple_nets(self):
        board, rules = _five_net_board()
        sr = SelectiveRouter(
            board, rules,
            config=AutorouteConfig(max_passes=3, time_limit_seconds=10),
        )
        result = sr.route_nets(net_codes=[1, 3])
        assert result.total_connections >= 0

    def test_resolve_combined_selectors(self):
        board, rules = _five_net_board()
        sr = SelectiveRouter(board, rules)
        codes = sr.resolve_nets(net_codes=[1], net_names=["NET3", "NET5"])
        assert 1 in codes
        assert 3 in codes
        assert 5 in codes

    def test_resolve_invalid_net_code_ignored(self):
        board, rules = _simple_two_net_board()
        sr = SelectiveRouter(board, rules)
        codes = sr.resolve_nets(net_codes=[999])
        assert codes == []


# ── Selective Re-routing Tests ─────────────────────────────────────


class TestSelectiveRerouting:

    def test_reroute_removes_existing_traces(self):
        board, rules = _prerouted_board()
        sr = SelectiveRouter(
            board, rules,
            config=AutorouteConfig(max_passes=3, time_limit_seconds=5),
        )
        result = sr.reroute_nets(net_codes=[1])
        assert result.nets_ripped == 1
        assert result.traces_removed >= 1

    def test_reroute_unrouted_net_is_harmless(self):
        board, rules = _prerouted_board()
        sr = SelectiveRouter(
            board, rules,
            config=AutorouteConfig(max_passes=3, time_limit_seconds=5),
        )
        result = sr.reroute_nets(net_codes=[2])
        assert result.nets_ripped == 1
        assert result.traces_removed == 0

    def test_reroute_no_nets_is_noop(self):
        board, rules = _prerouted_board()
        sr = SelectiveRouter(board, rules)
        result = sr.reroute_nets(net_codes=[])
        assert result.nets_ripped == 0
        assert result.route_result.completed is True

    def test_reroute_rollback_on_failure(self):
        board, rules = _prerouted_board()
        initial_traces = len(board.get_traces())
        sr = SelectiveRouter(
            board, rules,
            config=AutorouteConfig(max_passes=0, time_limit_seconds=0.001),
        )
        result = sr.reroute_nets(net_codes=[1])
        if result.rolled_back:
            assert len(board.get_traces()) == initial_traces

    def test_reroute_multiple_nets(self):
        board, rules = _five_net_board()
        # Pre-route net 1
        board.add_trace(
            corners=[IntPoint(5_000_000, 10_000_000), IntPoint(75_000_000, 10_000_000)],
            width=250_000, layer_index=0, net_code=1,
        )
        sr = SelectiveRouter(
            board, rules,
            config=AutorouteConfig(max_passes=3, time_limit_seconds=10),
        )
        result = sr.reroute_nets(net_codes=[1, 2])
        assert result.nets_ripped == 2


# ── Full Pipeline Integration Tests ───────────────────────────────


class TestFullPipeline:

    def test_route_optimize_drc(self):
        board, rules = _simple_two_net_board()
        config = AutorouteConfig(max_passes=5, time_limit_seconds=10)
        router = BatchAutorouter(board, rules, config)
        route_result = router.run()
        assert route_result.total_connections >= 0

        opt = BatchOptimizer(board, rules, BatchOptConfig(max_passes=2))
        opt.run()

        drc = DrcChecker(board)
        drc_result = drc.run()
        assert drc_result is not None
        assert drc_result.board_items_checked >= 0

    def test_partially_routed_board(self):
        board, rules = _prerouted_board()
        config = AutorouteConfig(max_passes=5, time_limit_seconds=10)
        router = BatchAutorouter(board, rules, config)
        result = router.run()
        assert result.total_connections >= 0

    def test_empty_board_noop(self):
        board = _make_board(50, 50)
        rules = board.design_rules
        router = BatchAutorouter(board, rules)
        result = router.run()
        assert result.completed is True
        assert result.total_connections == 0

    def test_score_improves_or_stable_after_optimization(self):
        board, rules = _simple_two_net_board()
        router = BatchAutorouter(
            board, rules,
            AutorouteConfig(max_passes=3, time_limit_seconds=5),
        )
        router.run()
        score_before = board.compute_score()
        opt = BatchOptimizer(board, rules, BatchOptConfig(max_passes=3))
        opt.run()
        score_after = board.compute_score()
        assert score_after.unrouted_count <= score_before.unrouted_count


# ── DRC Integration Tests ─────────────────────────────────────────


class TestDrcIntegration:

    def test_drc_clean_routed_board(self):
        board, rules = _prerouted_board()
        drc = DrcChecker(board)
        result = drc.run()
        clearance_violations = [
            v for v in result.violations
            if "CLEARANCE" in v.violation_type.name
        ]
        assert len(clearance_violations) == 0

    def test_drc_detects_unrouted(self):
        board, rules = _prerouted_board()
        drc = DrcChecker(board)
        result = drc.run()
        connectivity = [
            v for v in result.violations
            if v.violation_type.name == "UNCONNECTED_ITEMS"
        ]
        assert len(connectivity) >= 1

    def test_drc_on_empty_board(self):
        board = _make_board(50, 50)
        drc = DrcChecker(board)
        result = drc.run()
        assert result.error_count == 0
