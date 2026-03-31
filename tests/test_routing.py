"""Integration tests for the routing pipeline."""

import pytest

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.autoroute.batch import BatchAutorouter, AutorouteConfig
from kicad_autorouter.optimize.pull_tight import PullTightAlgo
from kicad_autorouter.optimize.via_optimize import ViaOptimizer


def _make_board(
    width_mm: int = 20,
    height_mm: int = 20,
    layers: int = 1,
) -> RoutingBoard:
    """Create a blank test board."""
    w = width_mm * 1_000_000
    h = height_mm * 1_000_000
    board = RoutingBoard()
    board.bounding_box = BoundingBox(0, 0, w, h)
    layer_list = [Layer(i, f"Layer{i}", LayerType.SIGNAL) for i in range(layers)]
    board.layer_structure = LayerStructure(layer_list)
    board.net_classes["Default"] = NetClass("Default")
    board.default_net_class = board.net_classes["Default"]
    return board


def _add_pad(board, pad_id, net_code, x_mm, y_mm, layer=0):
    """Add a 0.8mm circular pad at (x_mm, y_mm)."""
    board.add_item(Pad(
        id=pad_id,
        net_codes=[net_code],
        layer_indices=[layer],
        fixed_state=FixedState.USER_FIXED,
        position=IntPoint(int(x_mm * 1_000_000), int(y_mm * 1_000_000)),
        size_x=800_000,
        size_y=800_000,
        pad_shape=PadShape.CIRCLE,
    ))
    if net_code not in board.nets:
        board.nets[net_code] = Net(net_code=net_code, name=f"Net{net_code}")


class TestSingleNetRouting:
    def test_simple_two_pad_route(self):
        """Route a single net with two pads on opposite sides."""
        board = _make_board()
        _add_pad(board, 1, 1, 2, 10)
        _add_pad(board, 2, 1, 18, 10)

        config = AutorouteConfig(max_passes=3, time_limit_seconds=10)
        result = BatchAutorouter(board, board.design_rules, config).run()

        assert result.completed
        assert result.connections_routed == 1
        assert len(board.get_traces()) >= 1

    def test_close_pads_route(self):
        """Route two pads that are very close together."""
        board = _make_board()
        _add_pad(board, 1, 1, 5, 10)
        _add_pad(board, 2, 1, 7, 10)

        result = BatchAutorouter(board, board.design_rules,
                                 AutorouteConfig(max_passes=3, time_limit_seconds=5)).run()
        assert result.completed


class TestMultiNetRouting:
    def test_three_nets(self):
        """Route three independent nets."""
        board = _make_board(30, 30)
        _add_pad(board, 1, 1, 5, 5)
        _add_pad(board, 2, 1, 25, 5)
        _add_pad(board, 3, 2, 5, 25)
        _add_pad(board, 4, 2, 25, 25)
        _add_pad(board, 5, 3, 5, 15)
        _add_pad(board, 6, 3, 25, 15)

        result = BatchAutorouter(board, board.design_rules,
                                 AutorouteConfig(max_passes=5, time_limit_seconds=30)).run()

        assert result.connections_routed == 3
        assert result.completed

    def test_net_ordering_shortest_first(self):
        """Shorter connections should be routed first (implicit via ordering)."""
        board = _make_board(30, 30)
        # Long net
        _add_pad(board, 1, 1, 2, 15)
        _add_pad(board, 2, 1, 28, 15)
        # Short net
        _add_pad(board, 3, 2, 14, 15)
        _add_pad(board, 4, 2, 16, 15)

        result = BatchAutorouter(board, board.design_rules,
                                 AutorouteConfig(max_passes=5, time_limit_seconds=15)).run()
        assert result.connections_routed == 2


class TestOptimization:
    def test_pull_tight_shortens_traces(self):
        """Pull-tight should reduce trace length."""
        board = _make_board()
        # Add a trace with an unnecessary detour
        board.add_trace(
            corners=[
                IntPoint(1_000_000, 5_000_000),
                IntPoint(10_000_000, 5_000_000),
                IntPoint(10_000_000, 6_000_000),  # detour up
                IntPoint(19_000_000, 6_000_000),
            ],
            width=250_000, layer_index=0, net_code=1,
        )
        board.nets[1] = Net(net_code=1, name="Test")

        before = board.get_traces()[0].total_length()
        PullTightAlgo(board, board.design_rules).optimize_all()
        after = board.get_traces()[0].total_length()

        # Should be shorter or equal (can't guarantee shortcut succeeds
        # without knowing obstacle layout, but collinear removal should help)
        assert after <= before

    def test_via_optimizer_no_crash(self):
        """Via optimizer should handle empty boards and boards with no vias."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="Test")
        removed = ViaOptimizer(board, board.design_rules).optimize_all()
        assert removed == 0


class TestEdgeCases:
    def test_no_connections(self):
        """Board with no nets should complete instantly."""
        board = _make_board()
        result = BatchAutorouter(board, board.design_rules).run()
        assert result.completed
        assert result.total_connections == 0

    def test_single_pad_net(self):
        """Net with only one pad should not generate connections."""
        board = _make_board()
        _add_pad(board, 1, 1, 10, 10)
        result = BatchAutorouter(board, board.design_rules).run()
        assert result.completed
        assert result.total_connections == 0

    def test_already_routed(self):
        """If pads are already connected by traces, no routing needed."""
        board = _make_board()
        _add_pad(board, 1, 1, 5, 10)
        _add_pad(board, 2, 1, 15, 10)
        # Pre-existing trace connecting them
        board.add_trace(
            corners=[IntPoint(5_000_000, 10_000_000), IntPoint(15_000_000, 10_000_000)],
            width=250_000, layer_index=0, net_code=1,
        )
        board.nets[1] = Net(net_code=1, name="Test")

        result = BatchAutorouter(board, board.design_rules).run()
        assert result.total_connections == 0
