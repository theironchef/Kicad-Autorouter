"""Unit tests for geometry primitives."""

import math
import pytest

from kicad_autorouter.geometry.point import IntPoint, FloatPoint
from kicad_autorouter.geometry.vector import IntVector, FloatVector
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.geometry.octagon import IntOctagon


class TestIntPoint:
    def test_creation(self):
        p = IntPoint(100, 200)
        assert p.x == 100
        assert p.y == 200

    def test_equality(self):
        assert IntPoint(1, 2) == IntPoint(1, 2)
        assert IntPoint(1, 2) != IntPoint(3, 4)

    def test_distance_squared(self):
        p1 = IntPoint(0, 0)
        p2 = IntPoint(3, 4)
        assert p1.distance_squared(p2) == 25

    def test_distance_to(self):
        p1 = IntPoint(0, 0)
        p2 = IntPoint(3, 4)
        assert p1.distance_to(p2) == pytest.approx(5.0)

    def test_translate_by(self):
        p = IntPoint(10, 20)
        p2 = p.translate_by(5, -3)
        assert p2 == IntPoint(15, 17)


class TestBoundingBox:
    def test_creation(self):
        bb = BoundingBox(0, 0, 100, 200)
        assert bb.width == 100
        assert bb.height == 200

    def test_contains(self):
        bb = BoundingBox(10, 10, 50, 50)
        assert bb.contains(IntPoint(25, 25))
        assert bb.contains(IntPoint(10, 10))  # edge
        assert not bb.contains(IntPoint(5, 25))

    def test_intersects(self):
        bb1 = BoundingBox(0, 0, 10, 10)
        bb2 = BoundingBox(5, 5, 15, 15)
        bb3 = BoundingBox(20, 20, 30, 30)
        assert bb1.intersects(bb2)
        assert not bb1.intersects(bb3)

    def test_union(self):
        bb1 = BoundingBox(0, 0, 10, 10)
        bb2 = BoundingBox(5, 5, 20, 20)
        u = bb1.union(bb2)
        assert u == BoundingBox(0, 0, 20, 20)

    def test_center(self):
        bb = BoundingBox(0, 0, 100, 200)
        c = bb.center()
        assert c.x == pytest.approx(50.0)
        assert c.y == pytest.approx(100.0)

    def test_enlarge(self):
        bb = BoundingBox(10, 10, 20, 20)
        e = bb.enlarge(5)
        assert e == BoundingBox(5, 5, 25, 25)

    def test_from_center_and_radius(self):
        bb = BoundingBox.from_center_and_radius(IntPoint(100, 100), 50)
        assert bb == BoundingBox(50, 50, 150, 150)


class TestIntOctagon:
    def test_from_bbox(self):
        oct = IntOctagon.from_bbox(0, 0, 100, 100)
        assert oct.lx == 0
        assert oct.rx == 100
        assert not oct.is_empty()

    def test_contains(self):
        oct = IntOctagon.from_bbox(0, 0, 100, 100)
        assert oct.contains(IntPoint(50, 50))
        assert oct.contains(IntPoint(0, 0))
        assert not oct.contains(IntPoint(-1, 50))

    def test_from_center_and_radius(self):
        oct = IntOctagon.from_center_and_radius(500, 500, 100)
        assert oct.contains(IntPoint(500, 500))
        assert oct.contains(IntPoint(450, 500))
        assert not oct.contains(IntPoint(300, 500))

    def test_intersection(self):
        o1 = IntOctagon.from_bbox(0, 0, 100, 100)
        o2 = IntOctagon.from_bbox(50, 50, 150, 150)
        inter = o1.intersection(o2)
        assert inter is not None
        assert inter.contains(IntPoint(75, 75))

    def test_no_intersection(self):
        o1 = IntOctagon.from_bbox(0, 0, 10, 10)
        o2 = IntOctagon.from_bbox(20, 20, 30, 30)
        assert o1.intersection(o2) is None

    def test_enlarge(self):
        oct = IntOctagon.from_bbox(10, 10, 20, 20)
        e = oct.enlarge(5)
        assert e.lx == 5
        assert e.rx == 25
        assert e.contains(IntPoint(7, 15))

    def test_bounding_box(self):
        oct = IntOctagon.from_bbox(10, 20, 30, 40)
        bb = oct.bounding_box()
        assert bb == BoundingBox(10, 20, 30, 40)

    def test_center(self):
        oct = IntOctagon.from_bbox(0, 0, 100, 200)
        c = oct.center()
        assert c.x == pytest.approx(50.0)
        assert c.y == pytest.approx(100.0)

    def test_translate(self):
        oct = IntOctagon.from_bbox(0, 0, 10, 10)
        t = oct.translate(5, 5)
        assert t.lx == 5
        assert t.ly == 5
        assert t.rx == 15
        assert t.uy == 15
