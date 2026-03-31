"""
R-tree spatial index for efficient item lookup.

Replaces the simple quadtree with a proper R-tree that supports:
- Bulk loading via Sort-Tile-Recursive (STR) for optimal tree quality
- Minimum Bounding Rectangle (MBR) based splitting
- Fast region and point queries
- Insert, remove, and rebuild operations

All coordinates in nanometers.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Iterator

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.item import Item

logger = logging.getLogger(__name__)

# R-tree node capacity
_MIN_CHILDREN = 4
_MAX_CHILDREN = 16


@dataclass
class _RTreeEntry:
    """A leaf entry: an item plus its bounding box."""

    item: Item
    bbox: BoundingBox


@dataclass
class _RTreeNode:
    """An internal or leaf node in the R-tree."""

    bbox: BoundingBox
    entries: list[_RTreeEntry] = field(default_factory=list)    # leaf only
    children: list[_RTreeNode] = field(default_factory=list)    # internal only

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    @property
    def count(self) -> int:
        if self.is_leaf:
            return len(self.entries)
        return sum(c.count for c in self.children)

    def recalc_bbox(self):
        """Recalculate bounding box from children/entries."""
        if self.is_leaf:
            if not self.entries:
                return
            bb = self.entries[0].bbox
            for e in self.entries[1:]:
                bb = bb.union(e.bbox)
            self.bbox = bb
        else:
            if not self.children:
                return
            bb = self.children[0].bbox
            for c in self.children[1:]:
                bb = bb.union(c.bbox)
            self.bbox = bb


class RTreeIndex:
    """R-tree spatial index with STR bulk-loading.

    Provides the same interface as SearchTree but with better
    performance characteristics for large item counts.

    Usage::

        rtree = RTreeIndex()
        rtree.bulk_load(items)
        hits = rtree.query_region(bbox)
    """

    def __init__(self, board_bbox: BoundingBox | None = None):
        self._board_bbox = board_bbox or BoundingBox(
            -1_000_000_000, -1_000_000_000,
            1_000_000_000, 1_000_000_000,
        )
        self._root = _RTreeNode(bbox=self._board_bbox)
        self._item_count = 0
        # ID → entry for fast removal
        self._id_map: dict[int, _RTreeEntry] = {}

    @property
    def item_count(self) -> int:
        return self._item_count

    # ------------------------------------------------------------------
    # Bulk loading (Sort-Tile-Recursive)
    # ------------------------------------------------------------------

    def bulk_load(self, items: list[Item]):
        """Build the tree from scratch using STR bulk-loading.

        STR sorts items by x then tiles by y, producing a balanced tree
        with minimal overlap between nodes. Much faster than repeated insert
        for large item sets.
        """
        entries = []
        for item in items:
            bb = item.bounding_box()
            entry = _RTreeEntry(item=item, bbox=bb)
            entries.append(entry)
            self._id_map[item.id] = entry

        if not entries:
            self._root = _RTreeNode(bbox=self._board_bbox)
            self._item_count = 0
            return

        self._root = self._str_build(entries)
        self._item_count = len(entries)

    def _str_build(self, entries: list[_RTreeEntry]) -> _RTreeNode:
        """Recursively build R-tree nodes using Sort-Tile-Recursive."""
        if len(entries) <= _MAX_CHILDREN:
            node = _RTreeNode(bbox=entries[0].bbox)
            node.entries = entries
            node.recalc_bbox()
            return node

        # Number of leaf nodes needed
        n = len(entries)
        num_leaves = math.ceil(n / _MAX_CHILDREN)
        num_slices = max(1, math.ceil(math.sqrt(num_leaves)))
        slice_size = max(1, math.ceil(n / num_slices))

        # Sort by x-center, split into vertical slices
        entries.sort(key=lambda e: (e.bbox.x_min + e.bbox.x_max))
        child_nodes: list[_RTreeNode] = []

        for i in range(0, n, slice_size):
            x_slice = entries[i:i + slice_size]
            # Sort each slice by y-center, split into tiles
            x_slice.sort(key=lambda e: (e.bbox.y_min + e.bbox.y_max))
            for j in range(0, len(x_slice), _MAX_CHILDREN):
                tile = x_slice[j:j + _MAX_CHILDREN]
                leaf = _RTreeNode(bbox=tile[0].bbox)
                leaf.entries = tile
                leaf.recalc_bbox()
                child_nodes.append(leaf)

        # If only one node, return it directly
        if len(child_nodes) == 1:
            return child_nodes[0]

        # Recursively build internal nodes from child nodes
        return self._str_pack_internal(child_nodes)

    def _str_pack_internal(self, nodes: list[_RTreeNode]) -> _RTreeNode:
        """Pack a list of child nodes into an internal R-tree node hierarchy."""
        if len(nodes) <= _MAX_CHILDREN:
            parent = _RTreeNode(bbox=nodes[0].bbox)
            parent.children = nodes
            parent.recalc_bbox()
            return parent

        n = len(nodes)
        num_groups = math.ceil(n / _MAX_CHILDREN)
        num_slices = max(1, math.ceil(math.sqrt(num_groups)))
        slice_size = max(1, math.ceil(n / num_slices))

        nodes.sort(key=lambda nd: (nd.bbox.x_min + nd.bbox.x_max))
        parents: list[_RTreeNode] = []

        for i in range(0, n, slice_size):
            x_slice = nodes[i:i + slice_size]
            x_slice.sort(key=lambda nd: (nd.bbox.y_min + nd.bbox.y_max))
            for j in range(0, len(x_slice), _MAX_CHILDREN):
                group = x_slice[j:j + _MAX_CHILDREN]
                parent = _RTreeNode(bbox=group[0].bbox)
                parent.children = group
                parent.recalc_bbox()
                parents.append(parent)

        if len(parents) == 1:
            return parents[0]
        return self._str_pack_internal(parents)

    # ------------------------------------------------------------------
    # Single-item operations
    # ------------------------------------------------------------------

    def insert(self, item: Item):
        """Insert a single item. For bulk operations, prefer bulk_load()."""
        bb = item.bounding_box()
        entry = _RTreeEntry(item=item, bbox=bb)
        self._id_map[item.id] = entry
        self._insert_entry(self._root, entry)
        self._item_count += 1

    def _insert_entry(self, node: _RTreeNode, entry: _RTreeEntry):
        """Insert an entry into a node (simple leaf-insert strategy)."""
        if node.is_leaf:
            node.entries.append(entry)
            node.bbox = node.bbox.union(entry.bbox)
            if len(node.entries) > _MAX_CHILDREN:
                self._split_leaf(node)
        else:
            # Choose child with minimum area enlargement
            best = node.children[0]
            best_cost = self._enlargement_cost(best.bbox, entry.bbox)
            for child in node.children[1:]:
                cost = self._enlargement_cost(child.bbox, entry.bbox)
                if cost < best_cost:
                    best_cost = cost
                    best = child
            self._insert_entry(best, entry)
            node.bbox = node.bbox.union(entry.bbox)

    def _split_leaf(self, node: _RTreeNode):
        """Split an overfull leaf into two children."""
        entries = node.entries
        entries.sort(key=lambda e: (e.bbox.x_min + e.bbox.x_max))
        mid = len(entries) // 2

        left = _RTreeNode(bbox=entries[0].bbox)
        left.entries = entries[:mid]
        left.recalc_bbox()

        right = _RTreeNode(bbox=entries[mid].bbox)
        right.entries = entries[mid:]
        right.recalc_bbox()

        node.entries = []
        node.children = [left, right]
        node.recalc_bbox()

    def remove(self, item: Item) -> bool:
        """Remove an item by ID."""
        entry = self._id_map.pop(item.id, None)
        if entry is None:
            return False
        removed = self._remove_from(self._root, entry)
        if removed:
            self._item_count -= 1
        return removed

    def _remove_from(self, node: _RTreeNode, entry: _RTreeEntry) -> bool:
        if node.is_leaf:
            try:
                node.entries.remove(entry)
                node.recalc_bbox()
                return True
            except ValueError:
                return False
        for child in node.children:
            if child.bbox.intersects(entry.bbox):
                if self._remove_from(child, entry):
                    node.recalc_bbox()
                    return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def query_region(self, region: BoundingBox) -> list[Item]:
        """Find all items whose bounding box overlaps the region."""
        result: list[Item] = []
        self._query(self._root, region, result)
        return result

    def _query(self, node: _RTreeNode, region: BoundingBox, result: list[Item]):
        if not node.bbox.intersects(region):
            return
        if node.is_leaf:
            for entry in node.entries:
                if entry.bbox.intersects(region):
                    result.append(entry.item)
        else:
            for child in node.children:
                self._query(child, region, result)

    def query_point(self, point: IntPoint, radius: int) -> list[Item]:
        """Find all items near a point."""
        region = BoundingBox.from_center_and_radius(point, radius)
        return self.query_region(region)

    def get_overlapping_items(self, item: Item) -> list[Item]:
        """Find items whose bbox overlaps the given item."""
        candidates = self.query_region(item.bounding_box())
        return [c for c in candidates if c.id != item.id]

    def get_conflicting_items(self, item: Item, clearance: int = 0) -> list[Item]:
        """Find items that conflict (overlap + clearance) with the given item."""
        bbox = item.bounding_box().enlarge(clearance)
        candidates = self.query_region(bbox)
        return [c for c in candidates
                if c.id != item.id and not c.shares_net(item)]

    def rebuild(self, items: list[Item], board_bbox: BoundingBox):
        """Rebuild the tree from scratch."""
        self._board_bbox = board_bbox
        self._id_map.clear()
        self.bulk_load(items)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _enlargement_cost(existing: BoundingBox, new: BoundingBox) -> float:
        merged = existing.union(new)
        return merged.area() - existing.area()

    def tree_height(self) -> int:
        """Return the height of the tree (for diagnostics)."""
        return self._height(self._root)

    def _height(self, node: _RTreeNode) -> int:
        if node.is_leaf:
            return 1
        if not node.children:
            return 1
        return 1 + max(self._height(c) for c in node.children)
