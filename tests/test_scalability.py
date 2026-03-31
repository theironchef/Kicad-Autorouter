"""
Scalability / performance validation tests.

These tests verify the autorouter's data structures and algorithms
perform acceptably on larger boards (hundreds of nets/items).
They focus on timing and correctness at scale, not routing quality.
"""

from __future__ import annotations

import time
import pytest

from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.datastructures.rtree import RTreeIndex
from kicad_autorouter.drc.checker import DrcChecker
from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.optimize.batch_optimizer import BatchOptimizer, BatchOptConfig
from kicad_autorouter.rules.design_rules import DesignRules


# ── Helpers ────────────────────────────────────────────────────────


def _make_large_board(num_nets: int = 100, board_size_mm: int = 200):
    """Create a board with many nets, each having two pads."""
    board = RoutingBoard()
    bs = board_size_mm * 1_000_000
    board.bounding_box = BoundingBox(0, 0, bs, bs)
    board.layer_structure = LayerStructure([
        Layer(0, "F.Cu", LayerType.SIGNAL),
        Layer(1, "B.Cu", LayerType.SIGNAL),
    ])
    board.net_classes["Default"] = NetClass("Default")
    board.default_net_class = board.net_classes["Default"]
    board.design_rules = DesignRules(min_clearance=150_000)

    if num_nets == 0:
        return board, board.design_rules

    spacing = bs // (num_nets + 1)
    for i in range(1, num_nets + 1):
        board.nets[i] = Net(net_code=i, name=f"N{i}")
        y = spacing * i
        pa = Pad(
            id=0, net_codes=[i], layer_indices=[0],
            fixed_state=FixedState.SYSTEM_FIXED,
            position=IntPoint(5_000_000, y),
            size_x=1_500_000, size_y=1_500_000,
            pad_shape=PadShape.CIRCLE,
        )
        pb = Pad(
            id=0, net_codes=[i], layer_indices=[0],
            fixed_state=FixedState.SYSTEM_FIXED,
            position=IntPoint(bs - 5_000_000, y),
            size_x=1_500_000, size_y=1_500_000,
            pad_shape=PadShape.CIRCLE,
        )
        board.add_item(pa)
        board.add_item(pb)

    return board, board.design_rules


def _add_traces_to_board(board, num_nets, board_size_mm=200):
    """Add a direct trace for each net (simulating a routed board)."""
    bs = board_size_mm * 1_000_000
    spacing = bs // (num_nets + 1)
    for i in range(1, num_nets + 1):
        y = spacing * i
        board.add_trace(
            corners=[IntPoint(5_000_000, y), IntPoint(bs - 5_000_000, y)],
            width=250_000, layer_index=0, net_code=i,
        )


# ── Board Construction Tests ──────────────────────────────────────


class TestBoardConstruction:

    def test_100_net_board_construction(self):
        t0 = time.perf_counter()
        board, rules = _make_large_board(100)
        elapsed = time.perf_counter() - t0
        assert len(board.nets) == 100
        assert len(board.get_pads()) == 200
        assert elapsed < 1.0, f"100-net board took {elapsed:.2f}s (>1s)"

    def test_500_net_board_construction(self):
        t0 = time.perf_counter()
        board, rules = _make_large_board(500)
        elapsed = time.perf_counter() - t0
        assert len(board.nets) == 500
        assert len(board.get_pads()) == 1000
        assert elapsed < 2.0, f"500-net board took {elapsed:.2f}s (>2s)"

    def test_1000_net_board_construction(self):
        t0 = time.perf_counter()
        board, rules = _make_large_board(1000)
        elapsed = time.perf_counter() - t0
        assert len(board.nets) == 1000
        assert elapsed < 5.0, f"1000-net board took {elapsed:.2f}s (>5s)"


# ── R-tree Scalability ────────────────────────────────────────────


class TestRTreeScalability:

    def _make_traced_board(self, num_traces):
        """Create a board with num_traces traces for R-tree testing."""
        board = RoutingBoard()
        board.bounding_box = BoundingBox(0, 0, 500_000_000, 500_000_000)
        board.layer_structure = LayerStructure([
            Layer(0, "F.Cu", LayerType.SIGNAL),
        ])
        board.design_rules = DesignRules(min_clearance=150_000)
        for i in range(1, num_traces + 1):
            board.nets[i] = Net(net_code=i, name=f"N{i}")
            y = i * 100_000
            board.add_trace(
                corners=[IntPoint(0, y), IntPoint(50_000, y)],
                width=50_000, layer_index=0, net_code=i,
            )
        return board

    def test_bulk_load_1000_items(self):
        board = self._make_traced_board(1000)
        items = list(board.all_items())

        t0 = time.perf_counter()
        tree = RTreeIndex(board.bounding_box)
        tree.bulk_load(items)
        elapsed = time.perf_counter() - t0
        assert tree.item_count == 1000
        assert elapsed < 0.5, f"Bulk load 1000 took {elapsed:.2f}s"

    def test_query_1000_items(self):
        board = self._make_traced_board(1000)
        items = list(board.all_items())
        tree = RTreeIndex(board.bounding_box)
        tree.bulk_load(items)

        t0 = time.perf_counter()
        results = tree.query_region(BoundingBox(0, 45_000_000, 50_000, 55_000_000))
        elapsed = time.perf_counter() - t0
        assert len(results) > 0
        assert elapsed < 0.1, f"Query took {elapsed:.2f}s"

    def test_bulk_load_5000_items(self):
        board = self._make_traced_board(5000)
        items = list(board.all_items())

        t0 = time.perf_counter()
        tree = RTreeIndex(board.bounding_box)
        tree.bulk_load(items)
        elapsed = time.perf_counter() - t0
        assert tree.item_count == 5000
        assert elapsed < 2.0, f"Bulk load 5000 took {elapsed:.2f}s"


# ── DRC Scalability ───────────────────────────────────────────────


class TestDrcScalability:

    def test_drc_100_net_board(self):
        board, rules = _make_large_board(100)
        _add_traces_to_board(board, 100)

        t0 = time.perf_counter()
        result = DrcChecker(board).run()
        elapsed = time.perf_counter() - t0
        assert result is not None
        assert elapsed < 2.0, f"DRC took {elapsed:.2f}s on 100-net board"

    def test_drc_500_net_board(self):
        board, rules = _make_large_board(500)
        _add_traces_to_board(board, 500)

        t0 = time.perf_counter()
        result = DrcChecker(board).run()
        elapsed = time.perf_counter() - t0
        assert result is not None
        assert elapsed < 30.0, f"DRC took {elapsed:.2f}s on 500-net board"

    def test_drc_pads_only_no_crash(self):
        board, rules = _make_large_board(200)
        result = DrcChecker(board).run()
        assert result is not None


# ── Optimizer Scalability ─────────────────────────────────────────


class TestOptimizerScalability:

    def test_optimize_100_net_board(self):
        board, rules = _make_large_board(100)
        _add_traces_to_board(board, 100)

        t0 = time.perf_counter()
        opt = BatchOptimizer(board, rules, BatchOptConfig(max_passes=1))
        result = opt.run()
        elapsed = time.perf_counter() - t0
        assert result is not None
        assert elapsed < 2.0, f"Optimizer took {elapsed:.2f}s on 100 nets"

    def test_optimize_empty_board_fast(self):
        board, rules = _make_large_board(0)
        t0 = time.perf_counter()
        opt = BatchOptimizer(board, rules, BatchOptConfig(max_passes=1))
        result = opt.run()
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.1


# ── Board Operations at Scale ─────────────────────────────────────


class TestBoardOpsScalability:

    def test_unconnected_pad_pairs_100_nets(self):
        board, rules = _make_large_board(100)

        t0 = time.perf_counter()
        total_pairs = 0
        for nc in board.nets:
            if nc > 0:
                pairs = board.get_unconnected_pad_pairs(nc)
                total_pairs += len(pairs)
        elapsed = time.perf_counter() - t0

        assert total_pairs == 100
        assert elapsed < 1.0, f"Unconnected pairs took {elapsed:.2f}s"

    def test_get_traces_on_net_after_routing(self):
        board, rules = _make_large_board(200)
        _add_traces_to_board(board, 200)

        t0 = time.perf_counter()
        for nc in range(1, 201):
            traces = board.get_traces_on_net(nc)
            assert len(traces) >= 1
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"200 net queries took {elapsed:.2f}s"

    def test_compute_score_large_board(self):
        board, rules = _make_large_board(500)
        _add_traces_to_board(board, 500)

        t0 = time.perf_counter()
        score = board.compute_score()
        elapsed = time.perf_counter() - t0
        assert score.trace_count == 500
        assert elapsed < 1.0, f"Score computation took {elapsed:.2f}s"
