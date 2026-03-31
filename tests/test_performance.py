"""Tests for v0.6 — R-tree, batch optimizer, ChangedArea, and profiler."""

import time
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
from kicad_autorouter.board.changed_area import ChangedArea
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.datastructures.rtree import RTreeIndex
from kicad_autorouter.optimize.batch_optimizer import (
    BatchOptimizer, BatchOptimizerMultiThreaded, BatchOptConfig, BatchOptResult,
)
from kicad_autorouter.utils.profiler import (
    RoutingProfiler, BenchmarkTarget, check_benchmarks,
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


def _populate_board_with_traces(board, num_nets=10, traces_per_net=3):
    """Add many nets and traces for performance testing."""
    for n in range(1, num_nets + 1):
        board.nets[n] = Net(net_code=n, name=f"N{n}")
        for t in range(traces_per_net):
            y = n * 3 + t * 0.5
            _add_trace(board, n, [(2, y), (10, y), (20, y)], width_um=250)


# ===================================================================
# R-tree tests
# ===================================================================

class TestRTree:

    def test_bulk_load_and_query(self):
        """Bulk-loaded R-tree returns correct items for region query."""
        board = _make_board()
        _populate_board_with_traces(board, num_nets=5, traces_per_net=2)
        items = list(board.all_items())

        rtree = RTreeIndex(board.bounding_box)
        rtree.bulk_load(items)
        assert rtree.item_count == len(items)

        # Query a region that should contain some items
        region = BoundingBox(0, 0, 25_000_000, 10_000_000)
        hits = rtree.query_region(region)
        assert len(hits) > 0

    def test_bulk_load_empty(self):
        """Bulk loading with no items doesn't crash."""
        rtree = RTreeIndex()
        rtree.bulk_load([])
        assert rtree.item_count == 0
        assert rtree.query_region(BoundingBox(0, 0, 1000, 1000)) == []

    def test_single_insert_and_query(self):
        """Single-item insert works correctly."""
        board = _make_board()
        trace = _add_trace(board, 1, [(5, 5), (15, 5)])
        board.nets[1] = Net(net_code=1, name="N1")

        rtree = RTreeIndex(board.bounding_box)
        rtree.insert(trace)
        assert rtree.item_count == 1

        hits = rtree.query_region(trace.bounding_box())
        assert len(hits) == 1
        assert hits[0].id == trace.id

    def test_remove(self):
        """Removing an item makes it unfindable."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        trace = _add_trace(board, 1, [(5, 5), (15, 5)])

        rtree = RTreeIndex(board.bounding_box)
        rtree.insert(trace)
        assert rtree.item_count == 1

        rtree.remove(trace)
        assert rtree.item_count == 0
        assert rtree.query_region(trace.bounding_box()) == []

    def test_query_no_overlap(self):
        """Query for a region with no items returns empty."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        _add_trace(board, 1, [(5, 5), (15, 5)])
        items = list(board.all_items())

        rtree = RTreeIndex(board.bounding_box)
        rtree.bulk_load(items)

        # Query far away from the trace
        region = BoundingBox(40_000_000, 40_000_000, 50_000_000, 50_000_000)
        assert rtree.query_region(region) == []

    def test_query_point(self):
        """Point query finds nearby items."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        trace = _add_trace(board, 1, [(10, 10), (20, 10)])

        rtree = RTreeIndex(board.bounding_box)
        rtree.insert(trace)

        hits = rtree.query_point(IntPoint(15_000_000, 10_000_000), 1_000_000)
        assert len(hits) == 1

    def test_get_conflicting_items(self):
        """Conflicting items (different net, overlapping) are found."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        board.nets[2] = Net(net_code=2, name="N2")
        t1 = _add_trace(board, 1, [(5, 10), (20, 10)])
        t2 = _add_trace(board, 2, [(10, 9), (10, 11)])  # crosses t1

        rtree = RTreeIndex(board.bounding_box)
        rtree.bulk_load([t1, t2])

        conflicts = rtree.get_conflicting_items(t1, clearance=150_000)
        assert any(c.id == t2.id for c in conflicts)

    def test_tree_height_reasonable(self):
        """Tree height stays manageable even with many items."""
        board = _make_board()
        _populate_board_with_traces(board, num_nets=20, traces_per_net=5)
        items = list(board.all_items())

        rtree = RTreeIndex(board.bounding_box)
        rtree.bulk_load(items)

        # 100 items in a tree with max 16 children per node should be ~2-3 levels
        assert rtree.tree_height() <= 5

    def test_rebuild(self):
        """Rebuild replaces tree contents."""
        board = _make_board()
        board.nets[1] = Net(net_code=1, name="N1")
        t1 = _add_trace(board, 1, [(5, 5), (15, 5)])

        rtree = RTreeIndex(board.bounding_box)
        rtree.insert(t1)
        assert rtree.item_count == 1

        # Rebuild with different items
        board.nets[2] = Net(net_code=2, name="N2")
        t2 = _add_trace(board, 2, [(5, 20), (15, 20)])
        rtree.rebuild([t2], board.bounding_box)
        assert rtree.item_count == 1

        hits = rtree.query_region(t1.bounding_box())
        assert all(h.id != t1.id for h in hits)


# ===================================================================
# ChangedArea tests
# ===================================================================

class TestChangedArea:

    def test_no_changes(self):
        ca = ChangedArea()
        assert not ca.has_changes
        assert ca.region_count == 0
        assert ca.merged_region is None

    def test_mark_changed(self):
        ca = ChangedArea()
        ca.mark_changed(BoundingBox(0, 0, 100, 100))
        assert ca.has_changes
        assert ca.region_count == 1

    def test_overlaps(self):
        ca = ChangedArea()
        ca.mark_changed(BoundingBox(10, 10, 20, 20))
        assert ca.overlaps(BoundingBox(15, 15, 25, 25))
        assert not ca.overlaps(BoundingBox(100, 100, 200, 200))

    def test_merged_region(self):
        ca = ChangedArea()
        ca.mark_changed(BoundingBox(0, 0, 10, 10))
        ca.mark_changed(BoundingBox(20, 20, 30, 30))
        merged = ca.merged_region
        assert merged is not None
        assert merged.x_min == 0
        assert merged.x_max == 30

    def test_clear(self):
        ca = ChangedArea()
        ca.mark_changed(BoundingBox(0, 0, 10, 10))
        ca.clear()
        assert not ca.has_changes

    def test_mark_point_changed(self):
        ca = ChangedArea()
        ca.mark_point_changed(IntPoint(100, 100), 50)
        assert ca.has_changes
        assert ca.overlaps(BoundingBox(80, 80, 120, 120))

    def test_merge_regions(self):
        ca = ChangedArea()
        ca.mark_changed(BoundingBox(0, 0, 15, 15))
        ca.mark_changed(BoundingBox(10, 10, 25, 25))
        ca.mark_changed(BoundingBox(100, 100, 110, 110))

        merged = ca.merge_regions()
        # First two overlap → merge into one; third is separate
        assert len(merged) == 2

    def test_str_repr(self):
        ca = ChangedArea()
        assert "no changes" in str(ca)
        ca.mark_changed(BoundingBox(0, 0, 10, 10))
        assert "1 regions" in str(ca)


# ===================================================================
# Batch optimizer tests
# ===================================================================

class TestBatchOptimizer:

    def test_sequential_no_crash(self):
        """BatchOptimizer runs without crashing on a board with traces."""
        board = _make_board()
        for n in range(1, 4):
            board.nets[n] = Net(net_code=n, name=f"N{n}")
        # Add traces with unnecessary corners (pull-tight target)
        _add_trace(board, 1, [(5, 5), (5, 15), (10, 15), (15, 15)], width_um=250)
        _add_trace(board, 2, [(5, 25), (10, 25), (15, 25), (20, 25)], width_um=250)

        opt = BatchOptimizer(board, board.design_rules, BatchOptConfig(max_passes=2))
        result = opt.run()
        assert isinstance(result, BatchOptResult)
        assert result.passes_run >= 1

    def test_sequential_empty_board(self):
        """BatchOptimizer on empty board returns immediately."""
        board = _make_board()
        opt = BatchOptimizer(board, board.design_rules)
        result = opt.run()
        assert result.passes_run >= 1
        assert result.traces_improved == 0

    def test_multithreaded_no_crash(self):
        """BatchOptimizerMultiThreaded runs without crashing."""
        board = _make_board()
        for n in range(1, 4):
            board.nets[n] = Net(net_code=n, name=f"N{n}")
        _add_trace(board, 1, [(5, 5), (5, 15), (10, 15), (15, 15)], width_um=250)
        _add_trace(board, 2, [(5, 25), (10, 25), (15, 25), (20, 25)], width_um=250)

        config = BatchOptConfig(max_passes=2, num_threads=2)
        opt = BatchOptimizerMultiThreaded(board, board.design_rules, config)
        result = opt.run()
        assert isinstance(result, BatchOptResult)
        assert result.passes_run >= 1

    def test_multithreaded_empty_board(self):
        """Multi-threaded optimizer on empty board doesn't crash."""
        board = _make_board()
        config = BatchOptConfig(num_threads=2)
        opt = BatchOptimizerMultiThreaded(board, board.design_rules, config)
        result = opt.run()
        assert result.traces_improved == 0


# ===================================================================
# Profiler tests
# ===================================================================

class TestProfiler:

    def test_phase_timing(self):
        """Phase context manager records elapsed time."""
        profiler = RoutingProfiler()
        with profiler.phase("test_phase"):
            time.sleep(0.01)

        m = profiler.get_phase("test_phase")
        assert m is not None
        assert m.elapsed_ms > 5  # At least 5ms (sleep was 10ms)
        assert m.call_count == 1

    def test_multiple_calls(self):
        """Multiple calls accumulate time and call count."""
        profiler = RoutingProfiler()
        for _ in range(3):
            with profiler.phase("repeated"):
                pass

        m = profiler.get_phase("repeated")
        assert m.call_count == 3

    def test_item_count(self):
        """Item count is accumulated across calls."""
        profiler = RoutingProfiler()
        with profiler.phase("with_items", item_count=10):
            pass
        with profiler.phase("with_items", item_count=5):
            pass

        m = profiler.get_phase("with_items")
        assert m.item_count == 15
        assert m.call_count == 2

    def test_manual_record(self):
        """Manual recording adds to phase metrics."""
        profiler = RoutingProfiler()
        profiler.record("manual", elapsed_ms=42.5, item_count=7)
        m = profiler.get_phase("manual")
        assert m.elapsed_ms == pytest.approx(42.5)
        assert m.item_count == 7

    def test_summary(self):
        """Summary produces readable output."""
        profiler = RoutingProfiler()
        with profiler.phase("phase_a"):
            pass
        with profiler.phase("phase_b"):
            pass
        s = profiler.summary()
        assert "Routing Profile" in s
        assert "phase_a" in s
        assert "phase_b" in s

    def test_to_dict(self):
        """Export as dict for serialization."""
        profiler = RoutingProfiler()
        with profiler.phase("test"):
            pass
        d = profiler.to_dict()
        assert "total_ms" in d
        assert "test" in d["phases"]

    def test_reset(self):
        """Reset clears all phases."""
        profiler = RoutingProfiler()
        with profiler.phase("before"):
            pass
        profiler.reset()
        assert profiler.get_phase("before") is None

    def test_benchmark_pass(self):
        """Benchmark check passes when within target."""
        profiler = RoutingProfiler()
        profiler.record("fast_phase", elapsed_ms=5.0)
        targets = [BenchmarkTarget("fast_phase", max_ms=100.0)]
        failures = check_benchmarks(profiler, targets)
        assert len(failures) == 0

    def test_benchmark_fail(self):
        """Benchmark check fails when exceeding target."""
        profiler = RoutingProfiler()
        profiler.record("slow_phase", elapsed_ms=500.0)
        targets = [BenchmarkTarget("slow_phase", max_ms=100.0)]
        failures = check_benchmarks(profiler, targets)
        assert len(failures) >= 1
        assert "slow_phase" in failures[0]

    def test_benchmark_missing_phase(self):
        """Benchmark check reports missing phases."""
        profiler = RoutingProfiler()
        targets = [BenchmarkTarget("nonexistent", max_ms=100.0)]
        failures = check_benchmarks(profiler, targets)
        assert len(failures) == 1
        assert "not found" in failures[0]
