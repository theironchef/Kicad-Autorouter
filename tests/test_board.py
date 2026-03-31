"""Unit tests for board model."""

import pytest

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.board import RoutingBoard, BoardScore
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.via import Via
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.board.item import FixedState


class TestRoutingBoard:
    def _make_board(self) -> RoutingBoard:
        board = RoutingBoard()
        board.bounding_box = BoundingBox(0, 0, 10_000_000, 10_000_000)
        board.layer_structure = LayerStructure([
            Layer(0, "F.Cu", LayerType.SIGNAL),
            Layer(1, "B.Cu", LayerType.SIGNAL),
        ])
        board.nets[1] = Net(net_code=1, name="VCC")
        board.nets[2] = Net(net_code=2, name="SIG")
        board.net_classes["Default"] = NetClass("Default")
        board.default_net_class = board.net_classes["Default"]
        return board

    def test_add_and_get_item(self):
        board = self._make_board()
        pad = Pad(id=0, net_codes=[1], layer_indices=[0],
                  position=IntPoint(1000, 1000), size_x=500, size_y=500,
                  pad_shape=PadShape.CIRCLE)
        item_id = board.add_item(pad)
        assert item_id > 0
        assert board.get_item(item_id) is pad

    def test_remove_item(self):
        board = self._make_board()
        pad = Pad(id=0, net_codes=[1], layer_indices=[0],
                  position=IntPoint(1000, 1000), size_x=500, size_y=500,
                  pad_shape=PadShape.CIRCLE)
        item_id = board.add_item(pad)
        removed = board.remove_item(item_id)
        assert removed is pad
        assert board.get_item(item_id) is None

    def test_get_pads_on_net(self):
        board = self._make_board()
        board.add_item(Pad(id=1, net_codes=[1], layer_indices=[0],
                           position=IntPoint(100, 100), size_x=50, size_y=50,
                           pad_shape=PadShape.CIRCLE))
        board.add_item(Pad(id=2, net_codes=[1], layer_indices=[0],
                           position=IntPoint(200, 200), size_x=50, size_y=50,
                           pad_shape=PadShape.CIRCLE))
        board.add_item(Pad(id=3, net_codes=[2], layer_indices=[0],
                           position=IntPoint(300, 300), size_x=50, size_y=50,
                           pad_shape=PadShape.CIRCLE))
        assert len(board.get_pads_on_net(1)) == 2
        assert len(board.get_pads_on_net(2)) == 1

    def test_add_trace(self):
        board = self._make_board()
        trace = board.add_trace(
            corners=[IntPoint(0, 0), IntPoint(100, 0)],
            width=150_000, layer_index=0, net_code=1,
        )
        assert len(board.get_traces()) == 1
        assert trace.width == 150_000
        assert trace.net_code == 1

    def test_add_via(self):
        board = self._make_board()
        via = board.add_via(
            position=IntPoint(500, 500),
            diameter=800_000, drill=400_000,
            start_layer=0, end_layer=1, net_code=1,
        )
        assert len(board.get_vias()) == 1
        assert via.diameter == 800_000

    def test_remove_traces_on_net(self):
        board = self._make_board()
        board.add_trace([IntPoint(0, 0), IntPoint(100, 0)], 150_000, 0, 1)
        board.add_trace([IntPoint(0, 0), IntPoint(0, 100)], 150_000, 0, 2)
        assert len(board.get_traces()) == 2
        board.remove_traces_on_net(1)
        assert len(board.get_traces()) == 1
        assert board.get_traces()[0].net_code == 2

    def test_unconnected_pad_pairs(self):
        board = self._make_board()
        board.add_item(Pad(id=1, net_codes=[1], layer_indices=[0],
                           position=IntPoint(1_000_000, 5_000_000),
                           size_x=500_000, size_y=500_000, pad_shape=PadShape.CIRCLE))
        board.add_item(Pad(id=2, net_codes=[1], layer_indices=[0],
                           position=IntPoint(9_000_000, 5_000_000),
                           size_x=500_000, size_y=500_000, pad_shape=PadShape.CIRCLE))
        pairs = board.get_unconnected_pad_pairs(1)
        assert len(pairs) == 1


class TestBoardScore:
    def test_fewer_unrouted_is_better(self):
        s1 = BoardScore(unrouted_count=0, total_trace_length=1000, via_count=5, trace_count=10)
        s2 = BoardScore(unrouted_count=1, total_trace_length=500, via_count=2, trace_count=5)
        assert s1.is_better_than(s2)

    def test_fewer_vias_is_better_when_same_unrouted(self):
        s1 = BoardScore(unrouted_count=0, total_trace_length=1000, via_count=2, trace_count=10)
        s2 = BoardScore(unrouted_count=0, total_trace_length=500, via_count=5, trace_count=5)
        assert s1.is_better_than(s2)

    def test_shorter_traces_is_better_when_same_vias(self):
        s1 = BoardScore(unrouted_count=0, total_trace_length=500, via_count=2, trace_count=10)
        s2 = BoardScore(unrouted_count=0, total_trace_length=1000, via_count=2, trace_count=10)
        assert s1.is_better_than(s2)


class TestTrace:
    def test_total_length(self):
        t = Trace(id=1, net_codes=[1], layer_indices=[0],
                  corners=[IntPoint(0, 0), IntPoint(3_000_000, 0), IntPoint(3_000_000, 4_000_000)],
                  width=150_000, layer_index=0)
        assert t.total_length() == pytest.approx(7_000_000.0)

    def test_first_last_corner(self):
        t = Trace(id=1, corners=[IntPoint(0, 0), IntPoint(10, 20)],
                  width=100, layer_index=0)
        assert t.first_corner == IntPoint(0, 0)
        assert t.last_corner == IntPoint(10, 20)

    def test_empty_corners(self):
        t = Trace(id=1, corners=[], width=100, layer_index=0)
        assert t.first_corner is None
        assert t.last_corner is None

    def test_split_at(self):
        t = Trace(id=1, corners=[IntPoint(0, 0), IntPoint(5, 0), IntPoint(10, 0)],
                  width=100, layer_index=0)
        t1, t2 = t.split_at(1)
        assert len(t1.corners) == 2
        assert len(t2.corners) == 2
        assert t2.id == 0  # new trace gets ID 0


class TestVia:
    def test_is_through_2layer(self):
        v = Via(id=1, position=IntPoint(0, 0), start_layer=0, end_layer=1)
        v._total_layers = 2
        assert v.is_through

    def test_is_through_4layer(self):
        v = Via(id=1, position=IntPoint(0, 0), start_layer=0, end_layer=3)
        v._total_layers = 4
        assert v.is_through

    def test_is_blind(self):
        v = Via(id=1, position=IntPoint(0, 0), start_layer=0, end_layer=1)
        v._total_layers = 4
        assert v.is_blind

    def test_is_buried(self):
        v = Via(id=1, position=IntPoint(0, 0), start_layer=1, end_layer=2)
        v._total_layers = 4
        assert v.is_buried
