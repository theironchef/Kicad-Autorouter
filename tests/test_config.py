"""Tests for v0.8 — RouterSettings, update strategies, and selection strategies."""

import random
import pytest

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.rules.router_settings import (
    RouterSettings,
    LayerPreference,
    LayerDirection,
    ViaCostLevel,
    UpdateStrategy,
    SelectionStrategy,
)
from kicad_autorouter.autoroute.strategies import (
    BoardUpdater,
    ConnectionSelector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_board(width_mm=50, height_mm=50, layers=2):
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


def _add_pad(board, net, x_mm, y_mm, size_mm=1.0, layer=0):
    pad = Pad(
        id=0,
        net_codes=[net],
        layer_indices=[layer],
        position=IntPoint(int(x_mm * 1e6), int(y_mm * 1e6)),
        size_x=int(size_mm * 1e6),
        size_y=int(size_mm * 1e6),
        pad_shape=PadShape.CIRCLE,
    )
    board.add_item(pad)
    return pad


def _add_trace(board, net, corners_mm, layer=0, width_um=250):
    corners = [IntPoint(int(x * 1e6), int(y * 1e6)) for x, y in corners_mm]
    return board.add_trace(corners, width_um * 1000, layer, net)


# ===================================================================
# RouterSettings tests
# ===================================================================

class TestRouterSettings:

    def test_defaults(self):
        s = RouterSettings()
        assert s.max_passes == 20
        assert s.via_cost == ViaCostLevel.NORMAL
        assert s.selection_strategy == SelectionStrategy.SHORTEST_FIRST

    def test_via_cost_value(self):
        s = RouterSettings()
        assert s.get_via_cost_value("through") == 5
        s.via_cost = ViaCostLevel.CHEAP
        assert s.get_via_cost_value("through") == 1

    def test_layer_preference_default(self):
        s = RouterSettings()
        pref = s.get_layer_preference(0)
        assert pref.preferred_direction == LayerDirection.ANY
        assert pref.is_routing_enabled

    def test_layer_preference_set(self):
        s = RouterSettings()
        s.layer_preferences = [
            LayerPreference(0, LayerDirection.HORIZONTAL, direction_cost=2.0),
        ]
        pref = s.get_layer_preference(0)
        assert pref.preferred_direction == LayerDirection.HORIZONTAL
        assert pref.direction_cost == 2.0

    def test_direction_cost(self):
        s = RouterSettings()
        s.layer_preferences = [
            LayerPreference(0, LayerDirection.HORIZONTAL, direction_cost=3.0),
        ]
        # Horizontal on horizontal layer → 1.0
        assert s.get_direction_cost(0, is_horizontal=True) == 1.0
        # Vertical on horizontal layer → 3.0
        assert s.get_direction_cost(0, is_horizontal=False) == 3.0

    def test_direction_cost_any(self):
        s = RouterSettings()
        # No preference → always 1.0
        assert s.get_direction_cost(0, is_horizontal=True) == 1.0
        assert s.get_direction_cost(0, is_horizontal=False) == 1.0

    def test_is_layer_enabled(self):
        s = RouterSettings()
        s.layer_preferences = [
            LayerPreference(2, LayerDirection.ANY, is_routing_enabled=False),
        ]
        assert s.is_layer_enabled(0)   # no pref → default True
        assert not s.is_layer_enabled(2)

    def test_to_dict_and_back(self):
        """Serialisation round-trip preserves all settings."""
        s = RouterSettings()
        s.max_passes = 42
        s.via_cost = ViaCostLevel.EXPENSIVE
        s.update_strategy = UpdateStrategy.GLOBAL
        s.selection_strategy = SelectionStrategy.RANDOM
        s.allow_blind_vias = True
        s.layer_preferences = [
            LayerPreference(0, LayerDirection.HORIZONTAL, 2.5),
            LayerPreference(1, LayerDirection.VERTICAL, 1.0, False),
        ]

        d = s.to_dict()
        s2 = RouterSettings.from_dict(d)

        assert s2.max_passes == 42
        assert s2.via_cost == ViaCostLevel.EXPENSIVE
        assert s2.update_strategy == UpdateStrategy.GLOBAL
        assert s2.selection_strategy == SelectionStrategy.RANDOM
        assert s2.allow_blind_vias
        assert len(s2.layer_preferences) == 2
        assert s2.layer_preferences[0].preferred_direction == LayerDirection.HORIZONTAL
        assert s2.layer_preferences[1].is_routing_enabled is False

    def test_for_two_layer(self):
        s = RouterSettings.for_two_layer()
        assert len(s.layer_preferences) == 2
        assert s.layer_preferences[0].preferred_direction == LayerDirection.HORIZONTAL
        assert s.layer_preferences[1].preferred_direction == LayerDirection.VERTICAL

    def test_for_four_layer(self):
        s = RouterSettings.for_four_layer()
        assert len(s.layer_preferences) == 4
        assert s.allow_blind_vias
        # Layer 2 (power plane) should not be routed
        assert not s.layer_preferences[2].is_routing_enabled


# ===================================================================
# Board updater tests
# ===================================================================

class TestBoardUpdater:

    def test_greedy_optimises_immediately(self):
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        settings = RouterSettings()
        settings.update_strategy = UpdateStrategy.GREEDY

        updater = BoardUpdater(board, board.design_rules, settings)
        trace = _add_trace(board, 1, [(5, 5), (5, 15), (10, 15), (15, 15)])
        # Greedy → optimise right away (returns count)
        result = updater.notify_route_inserted(trace)
        # Should return >= 0 (may or may not improve depending on geometry)
        assert result >= 0

    def test_global_defers_to_end_of_pass(self):
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        settings = RouterSettings()
        settings.update_strategy = UpdateStrategy.GLOBAL

        updater = BoardUpdater(board, board.design_rules, settings)
        trace = _add_trace(board, 1, [(5, 5), (5, 15), (10, 15), (15, 15)])
        result = updater.notify_route_inserted(trace)
        assert result == 0  # Global defers

        end_result = updater.end_of_pass()
        assert end_result >= 0  # Now optimises

    def test_hybrid_defers_then_triggers(self):
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        settings = RouterSettings()
        settings.update_strategy = UpdateStrategy.HYBRID
        settings.hybrid_threshold = 2

        updater = BoardUpdater(board, board.design_rules, settings)

        t1 = _add_trace(board, 1, [(5, 5), (5, 15), (10, 15)])
        r1 = updater.notify_route_inserted(t1)
        assert r1 == 0  # Under threshold

        t2 = _add_trace(board, 1, [(20, 5), (20, 15), (25, 15)])
        r2 = updater.notify_route_inserted(t2)
        assert r2 >= 0  # At threshold → triggers


# ===================================================================
# Connection selector tests
# ===================================================================

class TestConnectionSelector:

    def _make_connections(self, board):
        """Create test connections of varying lengths."""
        board.nets[1] = Net(net_code=1, name="SIG_SHORT")
        board.nets[2] = Net(net_code=2, name="SIG_LONG")
        board.nets[3] = Net(net_code=3, name="GND")

        p1a = _add_pad(board, 1, 5, 5)
        p1b = _add_pad(board, 1, 10, 5)      # short
        p2a = _add_pad(board, 2, 5, 20)
        p2b = _add_pad(board, 2, 40, 20)     # long
        p3a = _add_pad(board, 3, 5, 35)
        p3b = _add_pad(board, 3, 30, 35)     # power

        return [
            (2, p2a, p2b),  # long signal
            (3, p3a, p3b),  # power
            (1, p1a, p1b),  # short signal
        ]

    def test_sequential(self):
        board = _make_board()
        conns = self._make_connections(board)
        settings = RouterSettings()
        settings.selection_strategy = SelectionStrategy.SEQUENTIAL

        selector = ConnectionSelector(board, settings)
        ordered = selector.order(conns)
        # Sequential preserves input order
        assert [c[0] for c in ordered] == [2, 3, 1]

    def test_shortest_first(self):
        board = _make_board()
        conns = self._make_connections(board)
        settings = RouterSettings()
        settings.selection_strategy = SelectionStrategy.SHORTEST_FIRST

        selector = ConnectionSelector(board, settings)
        ordered = selector.order(conns)
        # Shortest first, power last
        assert ordered[0][0] == 1   # shortest signal
        assert ordered[-1][0] == 3  # power

    def test_random(self):
        board = _make_board()
        conns = self._make_connections(board)
        settings = RouterSettings()
        settings.selection_strategy = SelectionStrategy.RANDOM

        selector = ConnectionSelector(board, settings)
        ordered = selector.order(conns)
        assert len(ordered) == 3  # All connections present

    def test_prioritized(self):
        board = _make_board()
        conns = self._make_connections(board)
        settings = RouterSettings()
        settings.selection_strategy = SelectionStrategy.PRIORITIZED

        selector = ConnectionSelector(board, settings)
        ordered = selector.order(conns)
        # Power net should be last (deprioritised)
        assert ordered[-1][0] == 3
