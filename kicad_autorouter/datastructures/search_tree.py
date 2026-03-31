"""
Spatial search tree for fast item lookup.

Uses an R-tree-like spatial index to efficiently find items that overlap
a given region. This is essential for the autorouter's collision detection
and expansion room computation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.item import Item

logger = logging.getLogger(__name__)

# Maximum items in a leaf node before splitting
_MAX_LEAF_SIZE = 16
# Maximum depth to prevent degenerate trees
_MAX_DEPTH = 30


@dataclass
class _TreeNode:
    """Internal node of the search tree."""
    bbox: BoundingBox
    items: list[Item] = field(default_factory=list)
    children: list[_TreeNode] = field(default_factory=list)
    depth: int = 0

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def insert(self, item: Item, item_bbox: BoundingBox):
        """Insert an item into this node."""
        if self.is_leaf:
            self.items.append(item)
            self.bbox = self.bbox.union(item_bbox)

            # Split if too many items
            if len(self.items) > _MAX_LEAF_SIZE and self.depth < _MAX_DEPTH:
                self._split()
        else:
            # Find best child (least enlargement)
            best_child = None
            best_enlargement = float('inf')
            for child in self.children:
                enlarged = child.bbox.union(item_bbox)
                enlargement = enlarged.area() - child.bbox.area()
                if enlargement < best_enlargement:
                    best_enlargement = enlargement
                    best_child = child

            if best_child:
                best_child.insert(item, item_bbox)
                self.bbox = self.bbox.union(item_bbox)

    def _split(self):
        """Split a leaf node into 4 quadrants."""
        cx = (self.bbox.x_min + self.bbox.x_max) // 2
        cy = (self.bbox.y_min + self.bbox.y_max) // 2

        quads = [
            BoundingBox(self.bbox.x_min, self.bbox.y_min, cx, cy),
            BoundingBox(cx, self.bbox.y_min, self.bbox.x_max, cy),
            BoundingBox(self.bbox.x_min, cy, cx, self.bbox.y_max),
            BoundingBox(cx, cy, self.bbox.x_max, self.bbox.y_max),
        ]

        self.children = [_TreeNode(bbox=q, depth=self.depth + 1) for q in quads]

        # Redistribute items
        for item in self.items:
            item_bbox = item.bounding_box()
            for child in self.children:
                if child.bbox.intersects(item_bbox):
                    child.items.append(item)
                    break
            else:
                # Item spans multiple quadrants; keep in first overlapping child
                self.children[0].items.append(item)

        self.items = []

    def query(self, region: BoundingBox, result: list[Item]):
        """Find all items overlapping a region."""
        if not self.bbox.intersects(region):
            return

        if self.is_leaf:
            for item in self.items:
                if item.bounding_box().intersects(region):
                    result.append(item)
        else:
            for child in self.children:
                child.query(region, result)

    def remove(self, item: Item) -> bool:
        """Remove an item from this node. Returns True if found."""
        if self.is_leaf:
            try:
                self.items.remove(item)
                return True
            except ValueError:
                return False
        else:
            for child in self.children:
                if child.remove(item):
                    return True
        return False


class SearchTree:
    """Spatial index for fast board item queries.

    Supports insert, remove, and region queries. Used by the autorouter
    for collision detection and finding nearby obstacles.
    """

    def __init__(self, board_bbox: BoundingBox | None = None):
        self._root = _TreeNode(
            bbox=board_bbox or BoundingBox(-1_000_000_000, -1_000_000_000,
                                           1_000_000_000, 1_000_000_000)
        )
        self._item_count = 0

    @property
    def item_count(self) -> int:
        return self._item_count

    def insert(self, item: Item):
        """Insert an item into the search tree."""
        self._root.insert(item, item.bounding_box())
        self._item_count += 1

    def remove(self, item: Item) -> bool:
        """Remove an item from the search tree."""
        if self._root.remove(item):
            self._item_count -= 1
            return True
        return False

    def query_region(self, region: BoundingBox) -> list[Item]:
        """Find all items overlapping a region."""
        result: list[Item] = []
        self._root.query(region, result)
        return result

    def query_point(self, point: IntPoint, radius: int) -> list[Item]:
        """Find all items near a point."""
        region = BoundingBox.from_center_and_radius(point, radius)
        return self.query_region(region)

    def get_overlapping_items(self, item: Item) -> list[Item]:
        """Find all items whose bounding box overlaps the given item."""
        candidates = self.query_region(item.bounding_box())
        return [c for c in candidates if c.id != item.id]

    def get_conflicting_items(self, item: Item, clearance: int = 0) -> list[Item]:
        """Find items that conflict (overlap + clearance) with the given item."""
        bbox = item.bounding_box().enlarge(clearance)
        candidates = self.query_region(bbox)
        return [c for c in candidates
                if c.id != item.id and not c.shares_net(item)]

    def rebuild(self, items: list[Item], board_bbox: BoundingBox):
        """Rebuild the tree from scratch with new items."""
        self._root = _TreeNode(bbox=board_bbox)
        self._item_count = 0
        for item in items:
            self.insert(item)
