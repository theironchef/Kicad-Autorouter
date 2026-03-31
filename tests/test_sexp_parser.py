"""
Unit tests for the S-expression KiCad board parser.

Tests parser functionality, error handling, and board construction.
"""

import pytest
from pathlib import Path

from kicad_autorouter.io.sexp_parser import SexpParser, SexpBoardParser
from kicad_autorouter.board.pad import PadShape


class TestSexpParser:
    """Test the low-level S-expression parser."""

    def test_parse_simple_atom(self):
        """Test parsing a simple unquoted atom."""
        parser = SexpParser("hello")
        result = parser.parse()
        assert result == "hello"

    def test_parse_integer(self):
        """Test parsing an integer."""
        parser = SexpParser("42")
        result = parser.parse()
        assert result == 42

    def test_parse_float(self):
        """Test parsing a float."""
        parser = SexpParser("3.14")
        result = parser.parse()
        assert result == 3.14

    def test_parse_quoted_string(self):
        """Test parsing a quoted string."""
        parser = SexpParser('"hello world"')
        result = parser.parse()
        assert result == "hello world"

    def test_parse_simple_list(self):
        """Test parsing a simple parenthesized list."""
        parser = SexpParser("(a b c)")
        result = parser.parse()
        assert result == ["a", "b", "c"]

    def test_parse_nested_list(self):
        """Test parsing nested lists."""
        parser = SexpParser("(a (b c) d)")
        result = parser.parse()
        assert result == ["a", ["b", "c"], "d"]

    def test_parse_mixed_types(self):
        """Test parsing lists with mixed types."""
        parser = SexpParser('(name "value" 42 3.14)')
        result = parser.parse()
        assert result == ["name", "value", 42, 3.14]

    def test_parse_with_whitespace(self):
        """Test parsing with extra whitespace."""
        parser = SexpParser("  (  a   b  )  ")
        result = parser.parse()
        assert result == ["a", "b"]

    def test_parse_unclosed_paren_raises(self):
        """Test that unclosed parenthesis raises ValueError."""
        parser = SexpParser("(a b")
        with pytest.raises(ValueError):
            parser.parse()

    def test_parse_unclosed_quote_raises(self):
        """Test that unclosed quote raises ValueError."""
        parser = SexpParser('"unclosed string')
        with pytest.raises(ValueError):
            parser.parse()


class TestSexpBoardParser:
    """Test the KiCad board parser."""

    @pytest.fixture
    def parser(self):
        """Create a fresh parser for each test."""
        return SexpBoardParser()

    @pytest.fixture
    def fixture_path(self):
        """Get path to test fixture."""
        return Path(__file__).parent / "fixtures" / "simple_two_net.kicad_pcb"

    def test_read_board_file(self, parser, fixture_path):
        """Test reading a complete board file."""
        board = parser.read(str(fixture_path))

        assert board is not None
        assert board.item_count > 0
        assert len(board.nets) > 0
        assert board.layer_structure.copper_layer_count > 0

    def test_board_has_correct_layer_count(self, parser, fixture_path):
        """Test that board layers are correctly extracted."""
        board = parser.read(str(fixture_path))

        # The fixture has F.Cu and B.Cu plus some non-copper layers
        copper_layers = [l for l in board.layer_structure.layers if "Cu" in l.name]
        assert len(copper_layers) >= 2

    def test_board_has_correct_nets(self, parser, fixture_path):
        """Test that nets are correctly extracted."""
        board = parser.read(str(fixture_path))

        net_names = {net.name for net in board.nets.values()}
        assert "GND" in net_names
        assert "VCC" in net_names

    def test_board_has_correct_components(self, parser, fixture_path):
        """Test that components are correctly extracted."""
        board = parser.read(str(fixture_path))

        assert len(board.components) == 2
        comp_refs = {c.reference for c in board.components.values()}
        assert "R1" in comp_refs
        assert "R2" in comp_refs

    def test_board_has_correct_pads(self, parser, fixture_path):
        """Test that pads are correctly extracted."""
        board = parser.read(str(fixture_path))

        pads = board.get_pads()
        assert len(pads) == 4  # 2 components x 2 pads each

        # Check that all pads are on a net
        for pad in pads:
            assert len(pad.net_codes) > 0

    def test_pad_positions_are_absolute(self, parser, fixture_path):
        """Test that pad positions are converted to absolute coordinates."""
        board = parser.read(str(fixture_path))

        # R1 is at (15, 15) mm
        # Its pad 1 is at (-0.9375, 0) relative = (14.0625, 15) absolute
        pads = board.get_pads()
        r1_pad = next((p for p in pads if p.component_ref == "R1" and p.pad_name == "1"), None)

        assert r1_pad is not None
        assert abs(r1_pad.position.x - 14_062_500) < 1000  # Allow 1 micrometer tolerance
        assert abs(r1_pad.position.y - 15_000_000) < 1000

    def test_pad_sizes_are_in_nanometers(self, parser, fixture_path):
        """Test that pad sizes are correctly converted to nanometers."""
        board = parser.read(str(fixture_path))

        # Fixture pads are 1.2 x 1.4 mm
        pads = board.get_pads()
        for pad in pads:
            assert pad.size_x == 1_200_000  # 1.2 mm
            assert pad.size_y == 1_400_000  # 1.4 mm

    def test_pad_shape_mapping(self, parser, fixture_path):
        """Test that pad shapes are correctly mapped."""
        board = parser.read(str(fixture_path))

        pads = board.get_pads()
        # All pads in fixture are rectangular
        for pad in pads:
            assert pad.pad_shape == PadShape.RECTANGLE

    def test_bounding_box_from_edge_cuts(self, parser, fixture_path):
        """Test that board bounding box is extracted from Edge.Cuts."""
        board = parser.read(str(fixture_path))

        # Fixture defines (0, 0) to (50, 30) mm
        assert board.bounding_box.x_min == 0
        assert board.bounding_box.y_min == 0
        assert board.bounding_box.x_max == 50_000_000  # 50 mm in nm
        assert board.bounding_box.y_max == 30_000_000  # 30 mm in nm

    def test_board_has_design_rules(self, parser, fixture_path):
        """Test that design rules are initialized."""
        board = parser.read(str(fixture_path))

        assert board.design_rules is not None
        assert board.design_rules.min_clearance > 0
        assert board.design_rules.min_trace_width > 0

    def test_board_has_default_net_class(self, parser, fixture_path):
        """Test that default net class exists."""
        board = parser.read(str(fixture_path))

        assert "Default" in board.net_classes
        assert board.default_net_class is not None

    def test_missing_file_raises_ioerror(self, parser):
        """Test that missing file raises IOError."""
        with pytest.raises(IOError):
            parser.read("/nonexistent/file.kicad_pcb")

    def test_pads_are_system_fixed(self, parser, fixture_path):
        """Test that pads are marked as system fixed (don't move during routing)."""
        board = parser.read(str(fixture_path))

        from kicad_autorouter.board.item import FixedState
        for pad in board.get_pads():
            assert pad.fixed_state == FixedState.SYSTEM_FIXED

    def test_pad_component_reference_is_set(self, parser, fixture_path):
        """Test that pads have their component reference."""
        board = parser.read(str(fixture_path))

        pads = board.get_pads()
        for pad in pads:
            assert pad.component_ref in ("R1", "R2")
            assert pad.pad_name in ("1", "2")

    def test_component_has_pad_ids(self, parser, fixture_path):
        """Test that components reference their pads."""
        board = parser.read(str(fixture_path))

        for comp in board.components.values():
            assert len(comp.pad_ids) == 2
            for pad_id in comp.pad_ids:
                pad = board.get_item(pad_id)
                assert pad is not None


class TestSexpParserEdgeCases:
    """Test edge cases and error handling."""

    def test_pad_shape_circle(self):
        """Test mapping of circle pad shape."""
        parser = SexpBoardParser()
        shape = parser._map_pad_shape("circle")
        assert shape == PadShape.CIRCLE

    def test_pad_shape_oval(self):
        """Test mapping of oval pad shape."""
        parser = SexpBoardParser()
        shape = parser._map_pad_shape("oval")
        assert shape == PadShape.OVAL

    def test_pad_shape_roundrect(self):
        """Test mapping of roundrect pad shape."""
        parser = SexpBoardParser()
        shape = parser._map_pad_shape("roundrect")
        assert shape == PadShape.ROUNDRECT

    def test_pad_shape_unknown_defaults_to_custom(self):
        """Test that unknown pad shapes map to CUSTOM."""
        parser = SexpBoardParser()
        shape = parser._map_pad_shape("unknown_shape")
        assert shape == PadShape.CUSTOM

    def test_to_number_converts_int(self):
        """Test number conversion for integers."""
        assert SexpBoardParser._to_number(42) == 42.0

    def test_to_number_converts_float(self):
        """Test number conversion for floats."""
        assert SexpBoardParser._to_number(3.14) == 3.14

    def test_to_number_converts_string(self):
        """Test number conversion for string numbers."""
        assert SexpBoardParser._to_number("42") == 42.0
        assert SexpBoardParser._to_number("3.14") == 3.14

    def test_to_number_returns_zero_for_invalid(self):
        """Test that invalid values return 0.0."""
        assert SexpBoardParser._to_number("invalid") == 0.0
        assert SexpBoardParser._to_number(None) == 0.0
