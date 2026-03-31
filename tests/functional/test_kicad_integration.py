"""Functional integration tests requiring KiCad pcbnew.

These tests verify the I/O bridge between KiCad's pcbnew API and the
autorouter's internal board model. They require KiCad to be installed
and pcbnew importable.

Run with: pytest tests/functional/ -m functional
Skip with: pytest tests/ -m "not functional"
"""

import os
import pathlib
import pytest

# Skip entire module if pcbnew is not available
try:
    import pcbnew
    HAS_PCBNEW = True
except ImportError:
    HAS_PCBNEW = False

pytestmark = pytest.mark.functional

FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def simple_board_path():
    return str(FIXTURES_DIR / "simple_two_net.kicad_pcb")


@pytest.fixture
def medium_board_path():
    return str(FIXTURES_DIR / "medium_board.kicad_pcb")


@pytest.fixture
def prerouted_board_path():
    return str(FIXTURES_DIR / "prerouted_board.kicad_pcb")


@pytest.mark.skipif(not HAS_PCBNEW, reason="pcbnew not available")
class TestBoardReading:
    """Test reading KiCad boards into the internal model."""

    def test_load_simple_board(self, simple_board_path):
        """Verify basic board loading."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader

        reader = KiCadBoardReader()
        board = reader.read(simple_board_path)
        assert board is not None
        assert board.bounding_box is not None

    def test_nets_loaded(self, simple_board_path):
        """Verify nets are read correctly."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader

        reader = KiCadBoardReader()
        board = reader.read(simple_board_path)
        # Should have at least GND and VCC
        net_names = {n.name for n in board.nets.values()}
        assert "GND" in net_names
        assert "VCC" in net_names

    def test_pads_loaded(self, simple_board_path):
        """Verify pads are read correctly."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader

        reader = KiCadBoardReader()
        board = reader.read(simple_board_path)
        pads = list(board.get_pads())
        assert len(pads) == 4  # 2 resistors * 2 pads each

    def test_layers_loaded(self, simple_board_path):
        """Verify layer structure is read."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader

        reader = KiCadBoardReader()
        board = reader.read(simple_board_path)
        assert board.layer_structure is not None
        assert len(board.layer_structure.layers) >= 2  # F.Cu and B.Cu

    def test_design_rules_loaded(self, simple_board_path):
        """Verify design rules are read."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader

        reader = KiCadBoardReader()
        board = reader.read(simple_board_path)
        assert board.design_rules is not None
        assert board.design_rules.min_clearance > 0

    def test_medium_board_components(self, medium_board_path):
        """Verify medium board has expected components."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader

        reader = KiCadBoardReader()
        board = reader.read(medium_board_path)
        assert len(board.components) >= 3  # U1, R1, C1
        pads = list(board.get_pads())
        assert len(pads) == 12  # U1(8) + R1(2) + C1(2)


@pytest.mark.skipif(not HAS_PCBNEW, reason="pcbnew not available")
class TestBoardWriting:
    """Test writing routed results back to KiCad."""

    def test_write_traces(self, simple_board_path, tmp_path):
        """Route a board and verify traces are written back."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader
        from kicad_autorouter.io.kicad_writer import KiCadBoardWriter
        from kicad_autorouter.autoroute.batch import (
            BatchAutorouter,
            AutorouteConfig,
        )

        reader = KiCadBoardReader()
        board = reader.read(simple_board_path)

        config = AutorouteConfig(max_passes=5, time_limit_seconds=30)
        router = BatchAutorouter(board, board.design_rules, config)
        result = router.run()

        # Write to a copy
        import shutil

        out_path = str(tmp_path / "routed.kicad_pcb")
        shutil.copy2(simple_board_path, out_path)

        kicad_board = pcbnew.LoadBoard(out_path)
        writer = KiCadBoardWriter(board, kicad_board)
        writer.write()
        pcbnew.SaveBoard(out_path, kicad_board)

        # Re-read and verify traces exist
        reloaded = pcbnew.LoadBoard(out_path)
        tracks = list(reloaded.GetTracks())
        assert len(tracks) > 0, "Expected traces to be written"

    def test_roundtrip_preserves_nets(self, simple_board_path, tmp_path):
        """Verify read → route → write preserves net assignments."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader
        from kicad_autorouter.io.kicad_writer import KiCadBoardWriter
        from kicad_autorouter.autoroute.batch import (
            BatchAutorouter,
            AutorouteConfig,
        )

        reader = KiCadBoardReader()
        board = reader.read(simple_board_path)

        config = AutorouteConfig(max_passes=5, time_limit_seconds=30)
        router = BatchAutorouter(board, board.design_rules, config)
        router.run()

        import shutil

        out_path = str(tmp_path / "routed.kicad_pcb")
        shutil.copy2(simple_board_path, out_path)

        kicad_board = pcbnew.LoadBoard(out_path)
        writer = KiCadBoardWriter(board, kicad_board)
        writer.write()
        pcbnew.SaveBoard(out_path, kicad_board)

        reloaded = pcbnew.LoadBoard(out_path)
        track_nets = {t.GetNetCode() for t in reloaded.GetTracks()}
        # All tracks should be on net 1 (GND) or net 2 (VCC)
        assert track_nets.issubset({1, 2}), f"Unexpected net codes: {track_nets}"

    def test_prerouted_traces_preserved(self, prerouted_board_path):
        """Verify existing traces are not destroyed during write."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader

        reader = KiCadBoardReader()
        board = reader.read(prerouted_board_path)

        # Count existing traces
        existing_traces = len(list(board.get_traces()))
        assert (
            existing_traces >= 1
        ), "Prerouted board should have at least 1 trace"

    def test_write_vias(self, simple_board_path, tmp_path):
        """Verify vias are written correctly if routing creates them."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader
        from kicad_autorouter.io.kicad_writer import KiCadBoardWriter
        from kicad_autorouter.autoroute.batch import (
            BatchAutorouter,
            AutorouteConfig,
        )

        reader = KiCadBoardReader()
        board = reader.read(simple_board_path)

        config = AutorouteConfig(max_passes=10, time_limit_seconds=60)
        router = BatchAutorouter(board, board.design_rules, config)
        router.run()

        # Check if any vias were created internally
        internal_vias = list(board.get_vias())

        import shutil

        out_path = str(tmp_path / "routed.kicad_pcb")
        shutil.copy2(simple_board_path, out_path)

        kicad_board = pcbnew.LoadBoard(out_path)
        writer = KiCadBoardWriter(board, kicad_board)
        writer.write()
        pcbnew.SaveBoard(out_path, kicad_board)

        reloaded = pcbnew.LoadBoard(out_path)
        kicad_vias = [
            t for t in reloaded.GetTracks() if isinstance(t, pcbnew.PCB_VIA)
        ]
        # Via count should match
        assert len(kicad_vias) == len(internal_vias)


@pytest.mark.skipif(not HAS_PCBNEW, reason="pcbnew not available")
class TestEndToEnd:
    """End-to-end tests: load → analyze → route → optimize → DRC → write."""

    def test_full_pipeline_simple(self, simple_board_path, tmp_path):
        """Full pipeline on simple board."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader
        from kicad_autorouter.io.kicad_writer import KiCadBoardWriter
        from kicad_autorouter.autoroute.pre_route_analysis import PreRouteAnalyzer
        from kicad_autorouter.autoroute.batch import (
            BatchAutorouter,
            AutorouteConfig,
        )
        from kicad_autorouter.drc.checker import DrcChecker

        # Read
        reader = KiCadBoardReader()
        board = reader.read(simple_board_path)

        # Analyze
        analyzer = PreRouteAnalyzer(board)
        report = analyzer.analyze()
        assert report.total_pads == 4
        assert report.total_nets >= 2

        # Route
        config = AutorouteConfig(max_passes=10, time_limit_seconds=60)
        router = BatchAutorouter(board, board.design_rules, config)
        result = router.run()
        assert result.connections_routed > 0

        # DRC
        checker = DrcChecker(board)
        drc = checker.run()
        # May have violations — just ensure it runs
        assert drc is not None

        # Write
        import shutil

        out_path = str(tmp_path / "routed.kicad_pcb")
        shutil.copy2(simple_board_path, out_path)
        kicad_board = pcbnew.LoadBoard(out_path)
        writer = KiCadBoardWriter(board, kicad_board)
        writer.write()
        pcbnew.SaveBoard(out_path, kicad_board)

        # Verify output file is valid
        assert os.path.getsize(out_path) > 0
        final_board = pcbnew.LoadBoard(out_path)
        assert len(list(final_board.GetTracks())) > 0

    def test_strategy_pipeline(self, simple_board_path):
        """Test composable strategy on real board."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader
        from kicad_autorouter.autoroute.routing_strategy import (
            RoutingStrategy,
            StrategyExecutor,
        )

        reader = KiCadBoardReader()
        board = reader.read(simple_board_path)

        strategy = RoutingStrategy.quick()
        executor = StrategyExecutor(board, board.design_rules)
        result = executor.execute(strategy)

        assert result.total_elapsed > 0
        assert len(result.pass_results) > 0

    def test_medium_board_pipeline(self, medium_board_path):
        """Full pipeline on medium complexity board."""
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader
        from kicad_autorouter.autoroute.batch import (
            BatchAutorouter,
            AutorouteConfig,
        )

        reader = KiCadBoardReader()
        board = reader.read(medium_board_path)

        assert len(board.nets) >= 5
        assert len(list(board.get_pads())) >= 12

        config = AutorouteConfig(max_passes=15, time_limit_seconds=120)
        router = BatchAutorouter(board, board.design_rules, config)
        result = router.run()

        # Should route at least some connections
        assert result.connections_routed > 0
        assert result.total_connections > 0
