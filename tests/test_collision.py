"""Unit tests for collision detection."""

import pytest

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.octagon import IntOctagon
from kicad_autorouter.geometry.collision import (
    segments_intersect,
    segment_intersects_octagon,
    segment_clearance_to_segment,
    segment_clearance_to_octagon,
)


class TestSegmentsIntersect:
    def test_crossing(self):
        assert segments_intersect(
            IntPoint(0, 0), IntPoint(10, 10),
            IntPoint(0, 10), IntPoint(10, 0),
        )

    def test_parallel(self):
        assert not segments_intersect(
            IntPoint(0, 0), IntPoint(10, 0),
            IntPoint(0, 5), IntPoint(10, 5),
        )

    def test_t_intersection(self):
        assert segments_intersect(
            IntPoint(5, 0), IntPoint(5, 10),
            IntPoint(0, 5), IntPoint(10, 5),
        )

    def test_no_overlap(self):
        assert not segments_intersect(
            IntPoint(0, 0), IntPoint(3, 0),
            IntPoint(5, 0), IntPoint(10, 0),
        )

    def test_collinear_overlap(self):
        assert segments_intersect(
            IntPoint(0, 0), IntPoint(5, 0),
            IntPoint(3, 0), IntPoint(8, 0),
        )

    def test_endpoint_touch(self):
        assert segments_intersect(
            IntPoint(0, 0), IntPoint(5, 5),
            IntPoint(5, 5), IntPoint(10, 0),
        )

    def test_disjoint(self):
        assert not segments_intersect(
            IntPoint(0, 0), IntPoint(1, 0),
            IntPoint(100, 100), IntPoint(101, 100),
        )


class TestSegmentIntersectsOctagon:
    def test_segment_through_octagon(self):
        oct = IntOctagon.from_bbox(10, 10, 20, 20)
        assert segment_intersects_octagon(
            IntPoint(0, 15), IntPoint(30, 15), oct
        )

    def test_segment_outside_octagon(self):
        oct = IntOctagon.from_bbox(10, 10, 20, 20)
        assert not segment_intersects_octagon(
            IntPoint(0, 0), IntPoint(5, 0), oct
        )

    def test_segment_inside_octagon(self):
        oct = IntOctagon.from_bbox(0, 0, 100, 100)
        assert segment_intersects_octagon(
            IntPoint(20, 20), IntPoint(80, 80), oct
        )

    def test_segment_touches_edge(self):
        oct = IntOctagon.from_bbox(10, 10, 20, 20)
        assert segment_intersects_octagon(
            IntPoint(0, 10), IntPoint(30, 10), oct
        )

    def test_segment_passes_corner(self):
        oct = IntOctagon.from_bbox(10, 10, 20, 20)
        # Diagonal through the corner region
        assert segment_intersects_octagon(
            IntPoint(5, 5), IntPoint(25, 25), oct
        )

    def test_segment_misses_narrowly(self):
        oct = IntOctagon.from_bbox(10, 10, 20, 20)
        assert not segment_intersects_octagon(
            IntPoint(0, 25), IntPoint(25, 25), oct
        )


class TestClearance:
    def test_intersecting_segments_zero_clearance(self):
        d = segment_clearance_to_segment(
            IntPoint(0, 0), IntPoint(10, 10),
            IntPoint(0, 10), IntPoint(10, 0),
        )
        assert d == 0.0

    def test_parallel_segments_clearance(self):
        d = segment_clearance_to_segment(
            IntPoint(0, 0), IntPoint(10, 0),
            IntPoint(0, 5), IntPoint(10, 5),
        )
        assert d == pytest.approx(5.0)

    def test_octagon_clearance_intersecting(self):
        oct = IntOctagon.from_bbox(10, 10, 20, 20)
        d = segment_clearance_to_octagon(
            IntPoint(0, 15), IntPoint(30, 15), oct
        )
        assert d == 0.0

    def test_octagon_clearance_separated(self):
        oct = IntOctagon.from_bbox(10, 10, 20, 20)
        d = segment_clearance_to_octagon(
            IntPoint(0, 0), IntPoint(5, 0), oct
        )
        assert d > 0
