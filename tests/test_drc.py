"""Tests for the DRC engine — clearance, connectivity, via, and report checks."""

import json
import math
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
from kicad_autorouter.board.obstacle import ObstacleArea
from kicad_autorouter.rules.design_rules import DesignRules
from kicad_autorouter.drc.violations import (
    DrcResult, DrcViolation, ViolationType, Severity,
)
from kicad_autorouter.drc.checker import DrcChecker, DrcConfig
from kicad_autorouter.drc.report import (
    convert_nm, format_length, format_position, LengthUnit,
    export_text, export_kicad_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_board(width_mm=30, height_mm=30, layers=2):
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
    board.design_rules = DesignRules(min_clearance=150_000)  # 0.15mm
    board.nets[1] = Net(net_code=1, name="N1")
    board.nets[2] = Net(net_code=2, name="N2")
    board.nets[3] = Net(net_code=3, name="N3")
    return board


def _add_trace(board, net, corners_mm, layer=0, width_um=250):
    """Add a trace from corner coordinates in mm, width in µm."""
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


def _add_via(board, net, x_mm, y_mm, dia_um=800, drill_um=400):
    return board.add_via(
        IntPoint(int(x_mm * 1e6), int(y_mm * 1e6)),
        dia_um * 1000, drill_um * 1000,
        start_layer=0, end_layer=1, net_code=net,
    )


# ===================================================================
# Unit conversion tests
# ===================================================================

class TestUnitConversion:

    def test_nm_to_mm(self):
        assert convert_nm(1_000_000, LengthUnit.MILLIMETERS) == pytest.approx(1.0)

    def test_nm_to_mils(self):
        # 1 mil = 25400 nm
        assert convert_nm(25_400, LengthUnit.MILS) == pytest.approx(1.0)

    def test_nm_to_inches(self):
        assert convert_nm(25_400_000, LengthUnit.INCHES) == pytest.approx(1.0)

    def test_nm_to_um(self):
        assert convert_nm(1000, LengthUnit.MICROMETERS) == pytest.approx(1.0)

    def test_format_length_mm(self):
        s = format_length(500_000, LengthUnit.MILLIMETERS, precision=2)
        assert s == "0.50mm"

    def test_format_position(self):
        s = format_position(5_000_000, 10_000_000, LengthUnit.MILLIMETERS, precision=1)
        assert s == "(5.0mm, 10.0mm)"


# ===================================================================
# Violation model tests
# ===================================================================

class TestViolationModel:

    def test_violation_str(self):
        v = DrcViolation(
            violation_type=ViolationType.TRACE_TRACE_CLEARANCE,
            severity=Severity.ERROR,
            message="Too close",
            location=IntPoint(5_000_000, 10_000_000),
        )
        s = str(v)
        assert "ERROR" in s
        assert "TRACE_TRACE_CLEARANCE" in s
        assert "5.000mm" in s

    def test_result_counts(self):
        result = DrcResult(violations=[
            DrcViolation(ViolationType.TRACE_TRACE_CLEARANCE, Severity.ERROR, "e1"),
            DrcViolation(ViolationType.TRACE_TRACE_CLEARANCE, Severity.ERROR, "e2"),
            DrcViolation(ViolationType.DANGLING_TRACE, Severity.WARNING, "w1"),
        ])
        assert result.error_count == 2
        assert result.warning_count == 1
        assert result.has_errors

    def test_result_deduplicate(self):
        v = DrcViolation(
            ViolationType.TRACE_PAD_CLEARANCE, Severity.ERROR, "dup",
            location=IntPoint(1, 2), item_ids=(10, 20), layer_index=0,
        )
        result = DrcResult(violations=[v, v, v])
        deduped = result.deduplicate()
        assert len(deduped.violations) == 1

    def test_violations_of_type(self):
        result = DrcResult(violations=[
            DrcViolation(ViolationType.TRACE_TRACE_CLEARANCE, Severity.ERROR, "a"),
            DrcViolation(ViolationType.DANGLING_TRACE, Severity.WARNING, "b"),
            DrcViolation(ViolationType.TRACE_TRACE_CLEARANCE, Severity.ERROR, "c"),
        ])
        tt = result.violations_of_type(ViolationType.TRACE_TRACE_CLEARANCE)
        assert len(tt) == 2


# ===================================================================
# Clearance checker tests
# ===================================================================

class TestClearanceChecks:

    def test_trace_trace_violation(self):
        """Two parallel traces too close together should be flagged."""
        board = _make_board()
        # Net 1 trace at y=5mm, net 2 trace at y=5.2mm
        # Clearance: 0.2mm center-to-center minus 2 half-widths (0.125mm each) = -0.05mm
        # That's way less than the 0.15mm required clearance
        _add_trace(board, 1, [(2, 5), (20, 5)], width_um=250)
        _add_trace(board, 2, [(2, 5.2), (20, 5.2)], width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_connectivity=False, check_dangles=False,
            check_single_layer_vias=False, check_board_edge=False,
            check_hole_clearance=False,
        ))
        result = checker.run()
        tt = result.violations_of_type(ViolationType.TRACE_TRACE_CLEARANCE)
        assert len(tt) >= 1
        assert tt[0].severity == Severity.ERROR

    def test_trace_trace_ok(self):
        """Two traces far apart should produce no violations."""
        board = _make_board()
        _add_trace(board, 1, [(2, 5), (20, 5)], width_um=250)
        _add_trace(board, 2, [(2, 15), (20, 15)], width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_connectivity=False, check_dangles=False,
            check_single_layer_vias=False, check_board_edge=False,
            check_hole_clearance=False,
        ))
        result = checker.run()
        tt = result.violations_of_type(ViolationType.TRACE_TRACE_CLEARANCE)
        assert len(tt) == 0

    def test_same_net_no_violation(self):
        """Two traces on the same net shouldn't be flagged."""
        board = _make_board()
        _add_trace(board, 1, [(2, 5), (20, 5)], width_um=250)
        _add_trace(board, 1, [(2, 5.2), (20, 5.2)], width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_connectivity=False, check_dangles=False,
            check_single_layer_vias=False, check_board_edge=False,
            check_hole_clearance=False,
        ))
        result = checker.run()
        tt = result.violations_of_type(ViolationType.TRACE_TRACE_CLEARANCE)
        assert len(tt) == 0

    def test_different_layers_no_violation(self):
        """Traces on different layers shouldn't be flagged for clearance."""
        board = _make_board()
        _add_trace(board, 1, [(2, 5), (20, 5)], layer=0, width_um=250)
        _add_trace(board, 2, [(2, 5), (20, 5)], layer=1, width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_connectivity=False, check_dangles=False,
            check_single_layer_vias=False, check_board_edge=False,
            check_hole_clearance=False,
        ))
        result = checker.run()
        tt = result.violations_of_type(ViolationType.TRACE_TRACE_CLEARANCE)
        assert len(tt) == 0

    def test_trace_pad_violation(self):
        """Trace running too close to a different-net pad."""
        board = _make_board()
        _add_trace(board, 1, [(5, 10), (15, 10)], width_um=250)
        _add_pad(board, 2, 10, 10.2, size_mm=0.5)  # pad center 0.2mm from trace center

        checker = DrcChecker(board, DrcConfig(
            check_connectivity=False, check_dangles=False,
            check_single_layer_vias=False, check_board_edge=False,
            check_hole_clearance=False,
        ))
        result = checker.run()
        tp = result.violations_of_type(ViolationType.TRACE_PAD_CLEARANCE)
        assert len(tp) >= 1

    def test_trace_via_violation(self):
        """Trace running too close to a different-net via."""
        board = _make_board()
        _add_trace(board, 1, [(5, 10), (15, 10)], width_um=250)
        _add_via(board, 2, 10, 10.3, dia_um=800)

        checker = DrcChecker(board, DrcConfig(
            check_connectivity=False, check_dangles=False,
            check_single_layer_vias=False, check_board_edge=False,
            check_hole_clearance=False,
        ))
        result = checker.run()
        tv = result.violations_of_type(ViolationType.TRACE_VIA_CLEARANCE)
        assert len(tv) >= 1

    def test_via_via_violation(self):
        """Two vias from different nets placed too close together."""
        board = _make_board()
        _add_via(board, 1, 10, 10, dia_um=800)
        _add_via(board, 2, 10.5, 10, dia_um=800)  # 0.5mm apart, radius = 0.4mm each

        checker = DrcChecker(board, DrcConfig(
            check_connectivity=False, check_dangles=False,
            check_single_layer_vias=False, check_board_edge=False,
            check_hole_clearance=False,
        ))
        result = checker.run()
        vv = result.violations_of_type(ViolationType.VIA_VIA_CLEARANCE)
        assert len(vv) >= 1

    def test_pad_pad_violation(self):
        """Two pads from different nets placed too close."""
        board = _make_board()
        _add_pad(board, 1, 10, 10, size_mm=1.0)
        _add_pad(board, 2, 10.5, 10, size_mm=1.0)

        checker = DrcChecker(board, DrcConfig(
            check_connectivity=False, check_dangles=False,
            check_single_layer_vias=False, check_board_edge=False,
            check_hole_clearance=False,
        ))
        result = checker.run()
        pp = result.violations_of_type(ViolationType.PAD_PAD_CLEARANCE)
        assert len(pp) >= 1


# ===================================================================
# Hole clearance tests
# ===================================================================

class TestHoleClearance:

    def test_hole_trace_violation(self):
        """Via drill too close to a different-net trace."""
        board = _make_board()
        _add_trace(board, 1, [(5, 10), (15, 10)], width_um=250)
        _add_via(board, 2, 10, 10.15, dia_um=800, drill_um=400)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_connectivity=False,
            check_dangles=False, check_single_layer_vias=False,
            check_board_edge=False,
        ))
        result = checker.run()
        hv = result.violations_of_type(ViolationType.HOLE_CLEARANCE)
        assert len(hv) >= 1

    def test_hole_clearance_ok(self):
        """Via far from any trace — no hole clearance violation."""
        board = _make_board()
        _add_trace(board, 1, [(5, 5), (15, 5)], width_um=250)
        _add_via(board, 2, 10, 15, dia_um=800, drill_um=400)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_connectivity=False,
            check_dangles=False, check_single_layer_vias=False,
            check_board_edge=False,
        ))
        result = checker.run()
        hv = result.violations_of_type(ViolationType.HOLE_CLEARANCE)
        assert len(hv) == 0


# ===================================================================
# Connectivity tests
# ===================================================================

class TestConnectivity:

    def test_unconnected_pads(self):
        """Two pads on same net with no traces → unconnected violation."""
        board = _make_board()
        _add_pad(board, 1, 5, 10)
        _add_pad(board, 1, 25, 10)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_hole_clearance=False,
            check_dangles=False, check_single_layer_vias=False,
            check_board_edge=False,
        ))
        result = checker.run()
        uc = result.violations_of_type(ViolationType.UNCONNECTED_ITEMS)
        assert len(uc) >= 1

    def test_connected_pads(self):
        """Two pads connected by a trace → no unconnected violation."""
        board = _make_board()
        p1 = _add_pad(board, 1, 5, 10)
        p2 = _add_pad(board, 1, 25, 10)
        _add_trace(board, 1, [(5, 10), (25, 10)], width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_hole_clearance=False,
            check_dangles=False, check_single_layer_vias=False,
            check_board_edge=False,
        ))
        result = checker.run()
        uc = result.violations_of_type(ViolationType.UNCONNECTED_ITEMS)
        assert len(uc) == 0

    def test_single_pad_net_no_violation(self):
        """Net with only one pad shouldn't flag unconnected."""
        board = _make_board()
        _add_pad(board, 1, 5, 10)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_hole_clearance=False,
            check_dangles=False, check_single_layer_vias=False,
            check_board_edge=False,
        ))
        result = checker.run()
        uc = result.violations_of_type(ViolationType.UNCONNECTED_ITEMS)
        assert len(uc) == 0


# ===================================================================
# Dangling trace tests
# ===================================================================

class TestDanglingTraces:

    def test_dangling_endpoint(self):
        """Trace with one end not connected to anything → dangling warning."""
        board = _make_board()
        _add_pad(board, 1, 5, 10, size_mm=1.0)
        # Trace starts at pad but ends in empty space
        _add_trace(board, 1, [(5, 10), (20, 10)], width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_hole_clearance=False,
            check_connectivity=False, check_single_layer_vias=False,
            check_board_edge=False,
        ))
        result = checker.run()
        dt = result.violations_of_type(ViolationType.DANGLING_TRACE)
        assert len(dt) >= 1
        assert dt[0].severity == Severity.WARNING

    def test_fully_connected_no_dangle(self):
        """Trace connecting two pads should not be flagged."""
        board = _make_board()
        _add_pad(board, 1, 5, 10, size_mm=1.0)
        _add_pad(board, 1, 20, 10, size_mm=1.0)
        _add_trace(board, 1, [(5, 10), (20, 10)], width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_hole_clearance=False,
            check_connectivity=False, check_single_layer_vias=False,
            check_board_edge=False,
        ))
        result = checker.run()
        dt = result.violations_of_type(ViolationType.DANGLING_TRACE)
        assert len(dt) == 0


# ===================================================================
# Single-layer via tests
# ===================================================================

class TestSingleLayerVia:

    def test_via_with_traces_on_both_layers(self):
        """Via with traces on both layers is NOT single-layer."""
        board = _make_board()
        via = _add_via(board, 1, 10, 10, dia_um=800)
        _add_trace(board, 1, [(10, 10), (20, 10)], layer=0, width_um=250)
        _add_trace(board, 1, [(10, 10), (20, 10)], layer=1, width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_hole_clearance=False,
            check_connectivity=False, check_dangles=False,
            check_board_edge=False,
        ))
        result = checker.run()
        sl = result.violations_of_type(ViolationType.SINGLE_LAYER_VIA)
        assert len(sl) == 0

    def test_via_with_trace_on_one_layer(self):
        """Via with trace on only one layer is flagged."""
        board = _make_board()
        _add_via(board, 1, 10, 10, dia_um=800)
        _add_trace(board, 1, [(10, 10), (20, 10)], layer=0, width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_hole_clearance=False,
            check_connectivity=False, check_dangles=False,
            check_board_edge=False,
        ))
        result = checker.run()
        sl = result.violations_of_type(ViolationType.SINGLE_LAYER_VIA)
        assert len(sl) >= 1
        assert sl[0].severity == Severity.WARNING


# ===================================================================
# Board edge clearance tests
# ===================================================================

class TestBoardEdge:

    def test_trace_too_close_to_edge(self):
        """Trace near board edge flagged."""
        board = _make_board(width_mm=30, height_mm=30)
        # Board edge clearance default = 0.25mm
        # Trace at x=0.1mm, half-width=0.125mm → copper at -0.025mm from edge!
        _add_trace(board, 1, [(0.1, 10), (0.1, 20)], width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_hole_clearance=False,
            check_connectivity=False, check_dangles=False,
            check_single_layer_vias=False,
        ))
        result = checker.run()
        be = result.violations_of_type(ViolationType.BOARD_EDGE_CLEARANCE)
        assert len(be) >= 1

    def test_trace_well_inside_board(self):
        """Trace well inside board → no edge violation."""
        board = _make_board(width_mm=30, height_mm=30)
        _add_trace(board, 1, [(5, 10), (25, 10)], width_um=250)

        checker = DrcChecker(board, DrcConfig(
            check_clearances=False, check_hole_clearance=False,
            check_connectivity=False, check_dangles=False,
            check_single_layer_vias=False,
        ))
        result = checker.run()
        be = result.violations_of_type(ViolationType.BOARD_EDGE_CLEARANCE)
        assert len(be) == 0


# ===================================================================
# Full DRC integration test
# ===================================================================

class TestFullDrc:

    def test_clean_board(self):
        """Board with properly routed traces should be clean."""
        board = _make_board()
        _add_pad(board, 1, 5, 15, size_mm=1.0)
        _add_pad(board, 1, 25, 15, size_mm=1.0)
        _add_trace(board, 1, [(5, 15), (25, 15)], width_um=250)

        checker = DrcChecker(board)
        result = checker.run()
        assert result.error_count == 0

    def test_drc_no_crash_empty_board(self):
        """DRC on an empty board should not crash."""
        board = _make_board()
        checker = DrcChecker(board)
        result = checker.run()
        assert result.error_count == 0
        assert result.warning_count == 0


# ===================================================================
# Report export tests
# ===================================================================

class TestReportExport:

    def _make_result(self):
        return DrcResult(
            violations=[
                DrcViolation(
                    ViolationType.TRACE_TRACE_CLEARANCE, Severity.ERROR,
                    "Too close", location=IntPoint(5_000_000, 10_000_000),
                    layer_index=0, item_ids=(1, 2), net_codes=(1, 2),
                    actual_value=100_000, required_value=150_000,
                ),
                DrcViolation(
                    ViolationType.DANGLING_TRACE, Severity.WARNING,
                    "Dangling endpoint", location=IntPoint(20_000_000, 15_000_000),
                    layer_index=0, item_ids=(3,), net_codes=(1,),
                ),
            ],
            board_items_checked=42,
            nets_checked=5,
            elapsed_ms=12.5,
        )

    def test_text_report(self):
        result = self._make_result()
        text = export_text(result)
        assert "DRC Report" in text
        assert "ERRORS (1)" in text
        assert "WARNINGS (1)" in text
        assert "Too close" in text
        assert "Dangling endpoint" in text

    def test_kicad_json_report(self):
        result = self._make_result()
        json_str = export_kicad_json(result)
        data = json.loads(json_str)
        assert data["$schema"] == "https://schemas.kicad.org/drc.v1.json"
        assert len(data["violations"]) == 2  # both get put in violations
        assert data["violations"][0]["severity"] == "error"

    def test_text_report_empty(self):
        result = DrcResult()
        text = export_text(result)
        assert "No violations found" in text
