"""Tests for v0.7 — component movement, board history, and net operations."""

import pytest

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.via import Via
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.component import Component
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.board.move_component import MoveComponentAlgo, MoveResult
from kicad_autorouter.board.history import BoardHistory, BoardHistoryEntry
from kicad_autorouter.board.net_operations import (
    find_diff_pairs, compute_net_lengths, check_diff_pair_lengths,
    compute_net_priorities, LengthMatchGroup, NetLength, DiffPair,
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


def _add_trace(board, net, corners_mm, layer=0, width_um=250):
    corners = [IntPoint(int(x * 1e6), int(y * 1e6)) for x, y in corners_mm]
    return board.add_trace(corners, width_um * 1000, layer, net)


def _add_pad(board, net, x_mm, y_mm, size_mm=1.0, layer=0, component_id=-1):
    pad = Pad(
        id=0,
        net_codes=[net],
        layer_indices=[layer],
        position=IntPoint(int(x_mm * 1e6), int(y_mm * 1e6)),
        size_x=int(size_mm * 1e6),
        size_y=int(size_mm * 1e6),
        pad_shape=PadShape.CIRCLE,
        component_id=component_id,
    )
    board.add_item(pad)
    return pad


def _make_component_board():
    """Board with a component (U1) owning two pads."""
    board = _make_board()
    board.nets[1] = Net(net_code=1, name="N1")
    board.nets[2] = Net(net_code=2, name="N2")

    comp = Component(id=1, reference="U1", position=IntPoint(10_000_000, 10_000_000))
    board.components[1] = comp

    p1 = _add_pad(board, 1, 9, 10, component_id=1)
    p2 = _add_pad(board, 2, 11, 10, component_id=1)
    comp.pad_ids = [p1.id, p2.id]

    return board, comp


# ===================================================================
# Component movement tests
# ===================================================================

class TestMoveComponent:

    def test_move_success(self):
        """Moving a component shifts position and pads."""
        board, comp = _make_component_board()
        mover = MoveComponentAlgo(board)
        result = mover.move(1, dx=2_000_000, dy=0)
        assert result.success
        assert result.pads_moved == 2
        assert comp.position.x == 12_000_000

    def test_move_locked_fails(self):
        """Cannot move a locked component."""
        board, comp = _make_component_board()
        comp.is_locked = True
        mover = MoveComponentAlgo(board)
        result = mover.move(1, dx=1_000_000, dy=0)
        assert not result.success
        assert "locked" in result.message.lower()

    def test_move_nonexistent_fails(self):
        """Moving a component that doesn't exist fails gracefully."""
        board, _ = _make_component_board()
        mover = MoveComponentAlgo(board)
        result = mover.move(999, dx=1_000_000, dy=0)
        assert not result.success
        assert "not found" in result.message.lower()

    def test_move_blocked_by_fixed_item(self):
        """Move blocked by a user-fixed trace at the destination."""
        board, comp = _make_component_board()
        # Place a fixed trace at the destination
        trace = _add_trace(board, 2, [(12, 10), (18, 10)])
        trace.fixed_state = FixedState.USER_FIXED
        # Net 2 pad is already on this component, so use net 3
        board.nets[3] = Net(net_code=3, name="N3")
        trace.net_codes = [3]

        mover = MoveComponentAlgo(board)
        result = mover.move(1, dx=2_000_000, dy=0)
        assert not result.success
        assert len(result.collision_items) > 0

    def test_move_shoves_unfixed_traces(self):
        """Unfixed obstacle traces are removed to make room."""
        board, comp = _make_component_board()
        board.nets[3] = Net(net_code=3, name="N3")
        trace = _add_trace(board, 3, [(12, 10), (18, 10)])

        mover = MoveComponentAlgo(board)
        result = mover.move(1, dx=2_000_000, dy=0)
        assert result.success
        assert result.traces_removed >= 1

    def test_sorted_directions(self):
        """Get sorted move directions returns valid list."""
        board, _ = _make_component_board()
        mover = MoveComponentAlgo(board)
        dirs = mover.get_sorted_move_directions(1)
        assert len(dirs) == 8  # 8 cardinal+diagonal directions


# ===================================================================
# Board history tests
# ===================================================================

class TestBoardHistory:

    def test_snapshot_and_undo(self):
        """Undo restores to previous state."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        history = BoardHistory(board)

        _add_trace(board, 1, [(5, 5), (15, 5)])
        assert board.item_count == 1

        history.snapshot("before add second")
        _add_trace(board, 1, [(5, 15), (15, 15)])
        assert board.item_count == 2

        history.undo()
        assert board.item_count == 1

    def test_redo(self):
        """Redo re-applies an undone change."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        history = BoardHistory(board)

        history.snapshot("before add")
        _add_trace(board, 1, [(5, 5), (15, 5)])
        assert board.item_count == 1

        history.undo()
        assert board.item_count == 0

        history.redo()
        assert board.item_count == 1

    def test_multiple_undos(self):
        """Multiple undo steps work correctly."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        history = BoardHistory(board)

        history.snapshot("step 0")
        _add_trace(board, 1, [(5, 5), (10, 5)])
        history.snapshot("step 1")
        _add_trace(board, 1, [(5, 15), (10, 15)])
        history.snapshot("step 2")
        _add_trace(board, 1, [(5, 25), (10, 25)])
        assert board.item_count == 3

        history.undo()
        assert board.item_count == 2
        history.undo()
        assert board.item_count == 1
        history.undo()
        assert board.item_count == 0

    def test_undo_empty_returns_false(self):
        board = _make_board()
        history = BoardHistory(board)
        assert not history.undo()

    def test_redo_empty_returns_false(self):
        board = _make_board()
        history = BoardHistory(board)
        assert not history.redo()

    def test_save_and_restore(self):
        """Named checkpoints can be saved and restored."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        history = BoardHistory(board)

        _add_trace(board, 1, [(5, 5), (15, 5)])
        history.save("checkpoint_1")
        assert board.item_count == 1

        _add_trace(board, 1, [(5, 15), (15, 15)])
        assert board.item_count == 2

        history.restore("checkpoint_1")
        assert board.item_count == 1

    def test_restore_nonexistent_returns_false(self):
        board = _make_board()
        history = BoardHistory(board)
        assert not history.restore("nonexistent")

    def test_list_saves(self):
        board = _make_board()
        history = BoardHistory(board)
        history.save("a")
        history.save("b")
        assert "a" in history.list_saves()
        assert "b" in history.list_saves()

    def test_clear(self):
        board = _make_board()
        history = BoardHistory(board)
        history.snapshot("x")
        history.save("y")
        history.clear()
        assert not history.can_undo
        assert history.list_saves() == []

    def test_max_entries(self):
        """History trims oldest entries when exceeding max_entries."""
        board = _make_board()
        history = BoardHistory(board, max_entries=3)
        for i in range(5):
            history.snapshot(f"step {i}")
        assert history.undo_depth == 3  # Oldest 2 trimmed


# ===================================================================
# Differential pair detection tests
# ===================================================================

class TestDiffPairs:

    def test_find_plus_minus(self):
        """Detect diff pairs from +/- naming."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="USB_D+")
        board.nets[2] = Net(net_code=2, name="USB_D-")
        pairs = find_diff_pairs(board)
        assert len(pairs) == 1
        assert pairs[0].base_name == "USB_D"

    def test_find_p_n(self):
        """Detect diff pairs from P/N naming."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="ETH_TXP")
        board.nets[2] = Net(net_code=2, name="ETH_TXN")
        pairs = find_diff_pairs(board)
        assert len(pairs) == 1

    def test_find_underscore_p_n(self):
        """Detect diff pairs from _P/_N naming."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="HDMI_CLK_P")
        board.nets[2] = Net(net_code=2, name="HDMI_CLK_N")
        pairs = find_diff_pairs(board)
        assert len(pairs) == 1

    def test_no_diff_pairs(self):
        """No diff pairs found when names don't match."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="GND")
        board.nets[2] = Net(net_code=2, name="VCC")
        pairs = find_diff_pairs(board)
        assert len(pairs) == 0


# ===================================================================
# Length matching tests
# ===================================================================

class TestLengthMatching:

    def test_compute_net_lengths(self):
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        _add_trace(board, 1, [(0, 0), (10, 0)])  # 10mm = 10_000_000 nm
        lengths = compute_net_lengths(board)
        assert 1 in lengths
        assert lengths[1].total_length_nm == pytest.approx(10_000_000, rel=0.01)

    def test_length_match_group_pass(self):
        """Nets within tolerance pass."""
        group = LengthMatchGroup(name="test", net_codes=[1, 2], tolerance_nm=500_000)
        lengths = {
            1: NetLength(1, "A", total_length_nm=10_000_000),
            2: NetLength(2, "B", total_length_nm=10_200_000),
        }
        violations = group.check(lengths)
        assert len(violations) == 0

    def test_length_match_group_fail(self):
        """Nets exceeding tolerance fail."""
        group = LengthMatchGroup(name="test", net_codes=[1, 2], tolerance_nm=100_000)
        lengths = {
            1: NetLength(1, "A", total_length_nm=10_000_000),
            2: NetLength(2, "B", total_length_nm=11_000_000),
        }
        violations = group.check(lengths)
        assert len(violations) == 1
        assert violations[0].delta_nm == pytest.approx(1_000_000)

    def test_check_diff_pair_lengths(self):
        """Diff pair length check catches mismatched pairs."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="USB_D+")
        board.nets[2] = Net(net_code=2, name="USB_D-")
        _add_trace(board, 1, [(0, 0), (10, 0)])      # 10mm
        _add_trace(board, 2, [(0, 5), (20, 5)])      # 20mm
        violations = check_diff_pair_lengths(board, tolerance_nm=500_000)
        assert len(violations) >= 1


# ===================================================================
# Net priority tests
# ===================================================================

class TestNetPriority:

    def test_signal_before_power(self):
        """Signal nets are prioritized before power nets."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="SIG1")
        board.nets[2] = Net(net_code=2, name="GND")
        # Need at least 2 pads per net for priority
        _add_pad(board, 1, 5, 5)
        _add_pad(board, 1, 15, 5)
        _add_pad(board, 2, 5, 15)
        _add_pad(board, 2, 15, 15)

        priorities = compute_net_priorities(board)
        sig = next(p for p in priorities if p.net_code == 1)
        gnd = next(p for p in priorities if p.net_code == 2)
        assert sig.priority < gnd.priority

    def test_diff_pair_boosted(self):
        """Diff pair nets get priority boost."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="USB_D+")
        board.nets[2] = Net(net_code=2, name="USB_D-")
        board.nets[3] = Net(net_code=3, name="SIG1")
        # All need 2 pads, same span for fair comparison
        for net in [1, 2, 3]:
            _add_pad(board, net, 5, net * 5)
            _add_pad(board, net, 15, net * 5)

        priorities = compute_net_priorities(board)
        dp = next(p for p in priorities if p.net_code == 1)
        sig = next(p for p in priorities if p.net_code == 3)
        assert dp.priority <= sig.priority

    def test_empty_board(self):
        board = _make_board()
        priorities = compute_net_priorities(board)
        assert priorities == []
