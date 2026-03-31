"""Tests for v0.3 trace operations: splitting, combination, overlap, tail detection."""

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


def _make_trace(corners, net_code=1, width=250_000, layer=0, trace_id=0):
    """Helper to create a simple trace."""
    return Trace(
        id=trace_id,
        net_codes=[net_code],
        layer_indices=[layer],
        corners=corners,
        width=width,
        layer_index=layer,
    )


def _make_board():
    """Helper to create a basic board."""
    board = RoutingBoard()
    board.bounding_box = BoundingBox(0, 0, 30_000_000, 30_000_000)
    board.layer_structure = LayerStructure([Layer(0, "F.Cu", LayerType.SIGNAL)])
    board.net_classes["Default"] = NetClass("Default")
    board.default_net_class = board.net_classes["Default"]
    board.nets[1] = Net(net_code=1, name="TestNet")
    return board


class TestTraceSplitAtPoint:
    """Tests for Trace.split_at_point()."""

    def test_split_at_midpoint(self):
        """Splitting a horizontal trace at its midpoint produces two halves."""
        t = _make_trace([IntPoint(0, 0), IntPoint(10_000_000, 0)])
        result = t.split_at_point(IntPoint(5_000_000, 0))
        assert result is not None
        t1, t2 = result
        assert len(t1.corners) == 2
        assert len(t2.corners) == 2
        assert t1.last_corner.x == 5_000_000
        assert t2.first_corner.x == 5_000_000

    def test_split_at_corner(self):
        """Splitting at an existing corner works (degenerates to split_at)."""
        corners = [IntPoint(0, 0), IntPoint(5_000_000, 0), IntPoint(10_000_000, 0)]
        t = _make_trace(corners)
        result = t.split_at_point(IntPoint(5_000_000, 0))
        assert result is not None
        t1, t2 = result
        assert t1.last_corner == IntPoint(5_000_000, 0)
        assert t2.first_corner == IntPoint(5_000_000, 0)

    def test_split_far_from_trace_returns_none(self):
        """Splitting at a point far from the trace returns None."""
        t = _make_trace([IntPoint(0, 0), IntPoint(10_000_000, 0)])
        result = t.split_at_point(IntPoint(5_000_000, 5_000_000))
        assert result is None

    def test_split_preserves_net(self):
        """Both halves keep the original net code."""
        t = _make_trace([IntPoint(0, 0), IntPoint(10_000_000, 0)], net_code=42)
        result = t.split_at_point(IntPoint(5_000_000, 0))
        assert result is not None
        t1, t2 = result
        assert 42 in t1.net_codes
        assert 42 in t2.net_codes

    def test_split_multi_segment(self):
        """Splitting on the second segment of a 3-segment trace."""
        corners = [
            IntPoint(0, 0),
            IntPoint(5_000_000, 0),
            IntPoint(5_000_000, 5_000_000),
            IntPoint(10_000_000, 5_000_000),
        ]
        t = _make_trace(corners)
        result = t.split_at_point(IntPoint(5_000_000, 2_500_000))
        assert result is not None
        t1, t2 = result
        assert t1.last_corner.y == 2_500_000
        assert t2.first_corner.y == 2_500_000


class TestTraceCombination:
    """Tests for Trace.can_combine_with() and combine_with()."""

    def test_combine_end_to_start(self):
        """Two traces sharing last->first endpoint combine correctly."""
        t1 = _make_trace([IntPoint(0, 0), IntPoint(5_000_000, 0)])
        t2 = _make_trace([IntPoint(5_000_000, 0), IntPoint(10_000_000, 0)])
        assert t1.can_combine_with(t2)
        combined = t1.combine_with(t2)
        assert combined is not None
        assert len(combined.corners) == 3
        assert combined.first_corner == IntPoint(0, 0)
        assert combined.last_corner == IntPoint(10_000_000, 0)

    def test_combine_start_to_start(self):
        """Two traces sharing first endpoints combine (one gets reversed)."""
        t1 = _make_trace([IntPoint(5_000_000, 0), IntPoint(0, 0)])
        t2 = _make_trace([IntPoint(5_000_000, 0), IntPoint(10_000_000, 0)])
        assert t1.can_combine_with(t2)
        combined = t1.combine_with(t2)
        assert combined is not None
        assert len(combined.corners) == 3

    def test_no_combine_different_nets(self):
        """Traces on different nets cannot combine."""
        t1 = _make_trace([IntPoint(0, 0), IntPoint(5_000_000, 0)], net_code=1)
        t2 = _make_trace([IntPoint(5_000_000, 0), IntPoint(10_000_000, 0)], net_code=2)
        assert not t1.can_combine_with(t2)

    def test_no_combine_different_layers(self):
        """Traces on different layers cannot combine."""
        t1 = _make_trace([IntPoint(0, 0), IntPoint(5_000_000, 0)], layer=0)
        t2 = _make_trace([IntPoint(5_000_000, 0), IntPoint(10_000_000, 0)], layer=1)
        assert not t1.can_combine_with(t2)

    def test_no_combine_different_widths(self):
        """Traces with different widths cannot combine."""
        t1 = _make_trace([IntPoint(0, 0), IntPoint(5_000_000, 0)], width=250_000)
        t2 = _make_trace([IntPoint(5_000_000, 0), IntPoint(10_000_000, 0)], width=500_000)
        assert not t1.can_combine_with(t2)

    def test_no_combine_disjoint(self):
        """Traces that don't share an endpoint cannot combine."""
        t1 = _make_trace([IntPoint(0, 0), IntPoint(3_000_000, 0)])
        t2 = _make_trace([IntPoint(7_000_000, 0), IntPoint(10_000_000, 0)])
        assert not t1.can_combine_with(t2)


class TestTraceOverlap:
    """Tests for Trace.has_overlap_with()."""

    def test_overlapping_collinear_traces(self):
        """Two collinear traces that overlap spatially."""
        t1 = _make_trace([IntPoint(0, 0), IntPoint(10_000_000, 0)])
        t2 = _make_trace([IntPoint(5_000_000, 0), IntPoint(15_000_000, 0)])
        assert t1.has_overlap_with(t2)

    def test_non_overlapping_parallel(self):
        """Two parallel traces that don't overlap."""
        t1 = _make_trace([IntPoint(0, 0), IntPoint(10_000_000, 0)])
        t2 = _make_trace([IntPoint(0, 5_000_000), IntPoint(10_000_000, 5_000_000)])
        assert not t1.has_overlap_with(t2)

    def test_different_net_no_overlap(self):
        """Traces on different nets don't report as overlapping."""
        t1 = _make_trace([IntPoint(0, 0), IntPoint(10_000_000, 0)], net_code=1)
        t2 = _make_trace([IntPoint(5_000_000, 0), IntPoint(15_000_000, 0)], net_code=2)
        assert not t1.has_overlap_with(t2)


class TestTailDetection:
    """Tests for Trace.get_uncontacted_endpoints() and board-level find_tails()."""

    def test_both_endpoints_contacted(self):
        """Trace with both endpoints at contact points has no tails."""
        t = _make_trace([IntPoint(1_000_000, 0), IntPoint(9_000_000, 0)])
        contacts = [IntPoint(1_000_000, 0), IntPoint(9_000_000, 0)]
        assert len(t.get_uncontacted_endpoints(contacts)) == 0

    def test_one_tail(self):
        """Trace with one endpoint not at a contact point."""
        t = _make_trace([IntPoint(1_000_000, 0), IntPoint(9_000_000, 0)])
        contacts = [IntPoint(1_000_000, 0)]
        tails = t.get_uncontacted_endpoints(contacts)
        assert len(tails) == 1
        assert tails[0] == IntPoint(9_000_000, 0)

    def test_both_tails(self):
        """Trace with no contact points has two tails."""
        t = _make_trace([IntPoint(1_000_000, 0), IntPoint(9_000_000, 0)])
        tails = t.get_uncontacted_endpoints([])
        assert len(tails) == 2

    def test_board_find_tails(self):
        """Board-level tail detection finds dangling traces."""
        board = _make_board()
        # Add a pad at one end only
        pad = Pad(
            id=1, net_codes=[1], layer_indices=[0],
            fixed_state=FixedState.USER_FIXED,
            position=IntPoint(1_000_000, 5_000_000),
            size_x=800_000, size_y=800_000,
            pad_shape=PadShape.CIRCLE,
        )
        board.add_item(pad)
        # Trace connects to pad at one end, dangles at other
        board.add_trace(
            corners=[IntPoint(1_000_000, 5_000_000), IntPoint(10_000_000, 5_000_000)],
            width=250_000, layer_index=0, net_code=1,
        )
        tails = board.find_tails()
        assert len(tails) >= 1

    def test_board_combine_traces(self):
        """Board-level trace combination merges adjacent traces."""
        board = _make_board()
        t1 = board.add_trace(
            corners=[IntPoint(0, 0), IntPoint(5_000_000, 0)],
            width=250_000, layer_index=0, net_code=1,
        )
        t2 = board.add_trace(
            corners=[IntPoint(5_000_000, 0), IntPoint(10_000_000, 0)],
            width=250_000, layer_index=0, net_code=1,
        )
        before = len(board.get_traces())
        merged = board.combine_traces()
        after = len(board.get_traces())
        assert merged >= 1
        assert after < before


class TestTranslateSegment:
    """Tests for Trace.translate_segment()."""

    def test_shove_middle_segment(self):
        """Translating a segment moves its two endpoints."""
        corners = [
            IntPoint(0, 0),
            IntPoint(5_000_000, 0),
            IntPoint(5_000_000, 5_000_000),
            IntPoint(10_000_000, 5_000_000),
        ]
        t = _make_trace(corners)
        shoved = t.translate_segment(1, 0, 1_000_000)  # Push seg 1 up by 1mm
        assert shoved.corners[1] == IntPoint(5_000_000, 1_000_000)
        assert shoved.corners[2] == IntPoint(5_000_000, 6_000_000)
        # Other corners unchanged
        assert shoved.corners[0] == IntPoint(0, 0)
        assert shoved.corners[3] == IntPoint(10_000_000, 5_000_000)

    def test_shove_out_of_range(self):
        """Translating an invalid segment index returns original trace."""
        t = _make_trace([IntPoint(0, 0), IntPoint(10_000_000, 0)])
        result = t.translate_segment(5, 1000, 1000)
        assert result.corners == t.corners
