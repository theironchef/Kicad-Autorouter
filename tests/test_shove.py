"""Tests for the shove trace algorithm and via optimization."""

import pytest

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.via import Via
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.autoroute.shove import ShoveTraceAlgo, ShoveConfig
from kicad_autorouter.optimize.via_optimize import ViaOptimizer, ViaOptConfig


def _make_board(width_mm=30, height_mm=30, layers=1):
    board = RoutingBoard()
    w = width_mm * 1_000_000
    h = height_mm * 1_000_000
    if layers == 1:
        layer_list = [Layer(0, "F.Cu", LayerType.SIGNAL)]
    else:
        layer_list = [
            Layer(0, "F.Cu", LayerType.SIGNAL),
            Layer(1, "B.Cu", LayerType.SIGNAL),
        ]
    board.bounding_box = BoundingBox(0, 0, w, h)
    board.layer_structure = LayerStructure(layer_list)
    board.net_classes["Default"] = NetClass("Default")
    board.default_net_class = board.net_classes["Default"]
    board.nets[1] = Net(net_code=1, name="Net1")
    board.nets[2] = Net(net_code=2, name="Net2")
    return board


class TestShoveTraceAlgo:
    """Tests for ShoveTraceAlgo."""

    def test_no_conflict_succeeds(self):
        """Shove with no conflicting traces succeeds immediately."""
        board = _make_board()
        rules = board.design_rules
        shove = ShoveTraceAlgo(board, rules)

        result = shove.shove_for_segment(
            IntPoint(5_000_000, 5_000_000),
            IntPoint(15_000_000, 5_000_000),
            new_half_width=125_000,
            new_net_code=1,
            layer_index=0,
        )
        assert result.success
        assert len(result.shoved_traces) == 0

    def test_shove_unfixed_trace(self):
        """An unfixed trace in the path gets shoved aside."""
        board = _make_board()
        rules = board.design_rules
        # Place an existing trace on net 2 that crosses path of net 1
        existing = Trace(
            id=100, net_codes=[2], layer_indices=[0],
            fixed_state=FixedState.UNFIXED,
            corners=[
                IntPoint(10_000_000, 2_000_000),
                IntPoint(10_000_000, 8_000_000),
            ],
            width=250_000, layer_index=0,
        )
        board.add_item(existing)

        shove = ShoveTraceAlgo(board, rules)
        result = shove.shove_for_segment(
            IntPoint(5_000_000, 5_000_000),
            IntPoint(15_000_000, 5_000_000),
            new_half_width=125_000,
            new_net_code=1,
            layer_index=0,
        )
        assert result.success
        assert len(result.shoved_traces) >= 1

    def test_shove_user_fixed_fails(self):
        """A USER_FIXED trace cannot be shoved."""
        board = _make_board()
        rules = board.design_rules
        existing = Trace(
            id=100, net_codes=[2], layer_indices=[0],
            fixed_state=FixedState.USER_FIXED,
            corners=[
                IntPoint(10_000_000, 2_000_000),
                IntPoint(10_000_000, 8_000_000),
            ],
            width=250_000, layer_index=0,
        )
        board.add_item(existing)

        shove = ShoveTraceAlgo(board, rules)
        result = shove.shove_for_segment(
            IntPoint(5_000_000, 5_000_000),
            IntPoint(15_000_000, 5_000_000),
            new_half_width=125_000,
            new_net_code=1,
            layer_index=0,
        )
        assert not result.success

    def test_same_net_not_shoved(self):
        """Traces on the same net are not conflicts and don't need shoving."""
        board = _make_board()
        rules = board.design_rules
        existing = Trace(
            id=100, net_codes=[1], layer_indices=[0],
            fixed_state=FixedState.UNFIXED,
            corners=[
                IntPoint(10_000_000, 2_000_000),
                IntPoint(10_000_000, 8_000_000),
            ],
            width=250_000, layer_index=0,
        )
        board.add_item(existing)

        shove = ShoveTraceAlgo(board, rules)
        result = shove.shove_for_segment(
            IntPoint(5_000_000, 5_000_000),
            IntPoint(15_000_000, 5_000_000),
            new_half_width=125_000,
            new_net_code=1,  # Same net
            layer_index=0,
        )
        assert result.success
        assert len(result.shoved_traces) == 0

    def test_apply_shoves(self):
        """Applied shoves actually modify the board."""
        board = _make_board()
        rules = board.design_rules
        existing = Trace(
            id=100, net_codes=[2], layer_indices=[0],
            fixed_state=FixedState.UNFIXED,
            corners=[
                IntPoint(10_000_000, 2_000_000),
                IntPoint(10_000_000, 8_000_000),
            ],
            width=250_000, layer_index=0,
        )
        board.add_item(existing)

        shove = ShoveTraceAlgo(board, rules)
        result = shove.shove_for_segment(
            IntPoint(5_000_000, 5_000_000),
            IntPoint(15_000_000, 5_000_000),
            new_half_width=125_000,
            new_net_code=1,
            layer_index=0,
        )
        if result.success and result.shoved_traces:
            old_pos = existing.corners[0]
            shove.apply_shoves(result)
            new_trace = board.get_item(100)
            assert new_trace is not None
            # The trace should have moved
            assert new_trace.corners[0] != old_pos or new_trace.corners[1] != existing.corners[1]


class TestViaOptimizer:
    """Tests for expanded ViaOptimizer."""

    def test_remove_same_layer_via(self):
        """A via connecting traces on the same layer is redundant."""
        board = _make_board(layers=2)
        rules = board.design_rules

        # Two traces on same layer connected through a via
        board.add_trace(
            corners=[IntPoint(5_000_000, 5_000_000), IntPoint(10_000_000, 5_000_000)],
            width=250_000, layer_index=0, net_code=1,
        )
        board.add_trace(
            corners=[IntPoint(10_000_000, 5_000_000), IntPoint(15_000_000, 5_000_000)],
            width=250_000, layer_index=0, net_code=1,
        )
        board.add_via(
            position=IntPoint(10_000_000, 5_000_000),
            diameter=800_000, drill=400_000,
            start_layer=0, end_layer=1, net_code=1,
        )

        optimizer = ViaOptimizer(board, rules)
        removed = optimizer._remove_redundant()
        assert removed >= 1

    def test_keep_necessary_via(self):
        """A via connecting traces on different layers is NOT redundant."""
        board = _make_board(layers=2)
        rules = board.design_rules

        board.add_trace(
            corners=[IntPoint(5_000_000, 5_000_000), IntPoint(10_000_000, 5_000_000)],
            width=250_000, layer_index=0, net_code=1,
        )
        board.add_trace(
            corners=[IntPoint(10_000_000, 5_000_000), IntPoint(15_000_000, 5_000_000)],
            width=250_000, layer_index=1, net_code=1,
        )
        board.add_via(
            position=IntPoint(10_000_000, 5_000_000),
            diameter=800_000, drill=400_000,
            start_layer=0, end_layer=1, net_code=1,
        )

        optimizer = ViaOptimizer(board, rules)
        removed = optimizer._remove_redundant()
        assert removed == 0

    def test_via_optimizer_no_crash_with_config(self):
        """ViaOptimizer with custom config runs without error."""
        board = _make_board(layers=2)
        config = ViaOptConfig(
            remove_redundant=True,
            relocate_vias=False,  # Skip relocation for speed
        )
        optimizer = ViaOptimizer(board, board.design_rules, config)
        result = optimizer.optimize_all()
        assert result >= 0


class TestDesignRulesViaSelection:
    """Tests for DesignRules.select_via_type()."""

    def test_through_via(self):
        """Full layer span selects through via dimensions."""
        rules = DesignRules()
        nc = NetClass("Default")
        diam, drill, is_micro = rules.select_via_type(0, 1, 2, nc)
        assert diam >= rules.min_via_diameter
        assert drill >= rules.min_via_drill
        assert not is_micro

    def test_micro_via_when_allowed(self):
        """Adjacent-layer span with micro vias enabled selects micro via."""
        rules = DesignRules(allow_micro_vias=True)
        nc = NetClass("Default")
        diam, drill, is_micro = rules.select_via_type(0, 1, 4, nc)
        assert is_micro
        assert diam <= rules.min_via_diameter  # Micro vias are smaller

    def test_blind_via_when_allowed(self):
        """Outer-to-inner span with blind vias enabled returns valid dims."""
        rules = DesignRules(allow_blind_vias=True)
        nc = NetClass("Default")
        diam, drill, is_micro = rules.select_via_type(0, 2, 4, nc)
        assert diam >= rules.min_via_diameter
        assert not is_micro
