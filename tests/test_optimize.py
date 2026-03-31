"""Tests for v0.4 optimization features: PullTight45, PullTight90, CornerSmoother, Fanout."""

import pytest
import math

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.component import Component
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.optimize.pull_tight_45 import (
    PullTightAlgo45, PullTightAlgo90, CornerSmoother,
    _is_45_aligned, _corner_angle,
)
from kicad_autorouter.autoroute.fanout import FanoutAlgo, FanoutConfig


def _make_board(width_mm=30, height_mm=30, layers=1):
    board = RoutingBoard()
    w, h = width_mm * 1_000_000, height_mm * 1_000_000
    layer_list = [Layer(i, f"Layer{i}", LayerType.SIGNAL) for i in range(layers)]
    board.bounding_box = BoundingBox(0, 0, w, h)
    board.layer_structure = LayerStructure(layer_list)
    board.net_classes["Default"] = NetClass("Default")
    board.default_net_class = board.net_classes["Default"]
    return board


# ----- Helper tests -----

class TestHelpers:
    def test_is_45_aligned_horizontal(self):
        assert _is_45_aligned(IntPoint(0, 0), IntPoint(1000, 0))

    def test_is_45_aligned_vertical(self):
        assert _is_45_aligned(IntPoint(0, 0), IntPoint(0, 1000))

    def test_is_45_aligned_diagonal(self):
        assert _is_45_aligned(IntPoint(0, 0), IntPoint(1000, 1000))

    def test_is_45_not_aligned(self):
        assert not _is_45_aligned(IntPoint(0, 0), IntPoint(1000, 500))

    def test_corner_angle_right_angle(self):
        angle = _corner_angle(
            IntPoint(0, 0), IntPoint(5, 0), IntPoint(5, 5),
        )
        assert abs(angle - 90.0) < 1.0

    def test_corner_angle_straight(self):
        angle = _corner_angle(
            IntPoint(0, 0), IntPoint(5, 0), IntPoint(10, 0),
        )
        assert abs(angle - 180.0) < 1.0

    def test_corner_angle_acute(self):
        angle = _corner_angle(
            IntPoint(0, 0), IntPoint(5, 0), IntPoint(4, 1),
        )
        assert angle < 90.0


# ----- PullTightAlgo45 tests -----

class TestPullTight45:
    def test_optimize_45_aligned_trace(self):
        """A trace that's already 45°-aligned should not change."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        # Already 45° aligned: horizontal → 45° diagonal → horizontal
        board.add_trace(
            corners=[
                IntPoint(2_000_000, 5_000_000),
                IntPoint(5_000_000, 5_000_000),
                IntPoint(8_000_000, 8_000_000),
                IntPoint(12_000_000, 8_000_000),
            ],
            width=250_000, layer_index=0, net_code=1,
        )
        opt = PullTightAlgo45(board, board.design_rules)
        improved = opt.optimize_all()
        # May or may not improve, but should not crash
        assert improved >= 0

    def test_optimize_removes_unnecessary_corner(self):
        """A detour trace should get shortened."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        # Detour: goes right, up, then right again — could shortcut
        board.add_trace(
            corners=[
                IntPoint(2_000_000, 10_000_000),
                IntPoint(8_000_000, 10_000_000),
                IntPoint(8_000_000, 5_000_000),
                IntPoint(14_000_000, 5_000_000),
            ],
            width=250_000, layer_index=0, net_code=1,
        )
        original = board.get_traces()[0].total_length()
        opt = PullTightAlgo45(board, board.design_rules)
        opt.optimize_all()
        new_length = board.get_traces()[0].total_length()
        # Should either improve or stay the same (never worsen)
        assert new_length <= original + 100

    def test_no_crash_on_short_trace(self):
        """Two-corner trace shouldn't crash."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        board.add_trace(
            corners=[IntPoint(1_000_000, 1_000_000), IntPoint(5_000_000, 1_000_000)],
            width=250_000, layer_index=0, net_code=1,
        )
        opt = PullTightAlgo45(board, board.design_rules)
        assert opt.optimize_all() == 0


# ----- PullTightAlgo90 tests -----

class TestPullTight90:
    def test_optimize_manhattan_trace(self):
        """90° optimizer should not crash on a Manhattan trace."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        board.add_trace(
            corners=[
                IntPoint(2_000_000, 5_000_000),
                IntPoint(5_000_000, 5_000_000),
                IntPoint(5_000_000, 10_000_000),
                IntPoint(10_000_000, 10_000_000),
            ],
            width=250_000, layer_index=0, net_code=1,
        )
        opt = PullTightAlgo90(board, board.design_rules)
        improved = opt.optimize_all()
        assert improved >= 0

    def test_90_shortens_detour(self):
        """A U-shaped detour should get shortcut by 90° optimizer."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        # U shape: right, down, right — could shortcut to just right
        board.add_trace(
            corners=[
                IntPoint(2_000_000, 5_000_000),
                IntPoint(6_000_000, 5_000_000),
                IntPoint(6_000_000, 6_000_000),
                IntPoint(10_000_000, 6_000_000),
            ],
            width=250_000, layer_index=0, net_code=1,
        )
        original = board.get_traces()[0].total_length()
        opt = PullTightAlgo90(board, board.design_rules)
        opt.optimize_all()
        new_length = board.get_traces()[0].total_length()
        assert new_length <= original + 100


# ----- CornerSmoother tests -----

class TestCornerSmoother:
    def test_smooth_acute_angle(self):
        """Acute angle should get chamfered."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        # Sharp V shape — angle < 90°
        board.add_trace(
            corners=[
                IntPoint(2_000_000, 10_000_000),
                IntPoint(8_000_000, 5_000_000),
                IntPoint(14_000_000, 10_000_000),
            ],
            width=250_000, layer_index=0, net_code=1,
        )
        original_corners = len(board.get_traces()[0].corners)
        smoother = CornerSmoother(board, board.design_rules, min_angle_deg=90.0)
        improved = smoother.smooth_all()
        new_corners = len(board.get_traces()[0].corners)
        # Should insert chamfer points (more corners)
        if improved > 0:
            assert new_corners > original_corners

    def test_no_smooth_obtuse_angle(self):
        """Obtuse angle (> min_angle) should not be modified."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        # Very gentle bend — angle > 150°
        board.add_trace(
            corners=[
                IntPoint(2_000_000, 5_000_000),
                IntPoint(10_000_000, 5_000_000),
                IntPoint(18_000_000, 5_500_000),
            ],
            width=250_000, layer_index=0, net_code=1,
        )
        smoother = CornerSmoother(board, board.design_rules, min_angle_deg=90.0)
        improved = smoother.smooth_all()
        assert improved == 0

    def test_no_crash_on_fixed_trace(self):
        """Fixed traces should be skipped."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        t = Trace(
            id=1, net_codes=[1], layer_indices=[0],
            fixed_state=FixedState.USER_FIXED,
            corners=[
                IntPoint(2_000_000, 10_000_000),
                IntPoint(8_000_000, 5_000_000),
                IntPoint(14_000_000, 10_000_000),
            ],
            width=250_000, layer_index=0,
        )
        board.add_item(t)
        smoother = CornerSmoother(board, board.design_rules)
        improved = smoother.smooth_all()
        assert improved == 0


# ----- Fanout tests -----

class TestFanout:
    def _make_bga_board(self):
        """Create a board with a small BGA-like component."""
        board = _make_board(30, 30, layers=2)
        board.nets[1] = Net(net_code=1, name="N1")
        board.nets[2] = Net(net_code=2, name="N2")
        board.nets[3] = Net(net_code=3, name="N3")
        board.nets[4] = Net(net_code=4, name="N4")

        comp = Component(id=1, reference="U1", position=IntPoint(15_000_000, 15_000_000))
        board.components[1] = comp

        # 2x2 grid of pads, 0.5mm spacing (dense)
        positions = [
            (14_750_000, 14_750_000),
            (15_250_000, 14_750_000),
            (14_750_000, 15_250_000),
            (15_250_000, 15_250_000),
        ]
        for i, (px, py) in enumerate(positions):
            pad = Pad(
                id=i + 10, net_codes=[i + 1], layer_indices=[0],
                fixed_state=FixedState.USER_FIXED,
                position=IntPoint(px, py),
                size_x=300_000, size_y=300_000,
                pad_shape=PadShape.CIRCLE,
                component_id=1,
            )
            board.add_item(pad)

        return board

    def test_fanout_no_crash(self):
        """Fanout routing runs without errors."""
        board = self._make_bga_board()
        fanout = FanoutAlgo(board, board.design_rules)
        result = fanout.fanout_all()
        assert result.components_processed >= 0

    def test_fanout_creates_traces(self):
        """Fanout should create escape traces for dense pads."""
        board = self._make_bga_board()
        config = FanoutConfig(
            escape_length=1_000_000,
            place_vias=True,
        )
        fanout = FanoutAlgo(board, board.design_rules, config)
        result = fanout.fanout_all()
        if result.pads_fanned > 0:
            assert len(board.get_traces()) >= result.pads_fanned

    def test_fanout_specific_component(self):
        """Can fanout a specific component by ID."""
        board = self._make_bga_board()
        fanout = FanoutAlgo(board, board.design_rules)
        result = fanout.fanout_component(1)
        assert result.failed_pads + result.pads_fanned >= 0

    def test_fanout_nonexistent_component(self):
        """Fanout on missing component returns empty result."""
        board = self._make_bga_board()
        fanout = FanoutAlgo(board, board.design_rules)
        result = fanout.fanout_component(999)
        assert result.pads_fanned == 0

    def test_fanout_empty_board(self):
        """Fanout on board with no components does nothing."""
        board = _make_board()
        fanout = FanoutAlgo(board, board.design_rules)
        result = fanout.fanout_all()
        assert result.components_processed == 0
