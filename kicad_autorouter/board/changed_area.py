"""
ChangedArea — Incremental update tracking.

Tracks which regions of the board have been modified so that only
affected areas need to be rebuilt (expansion graph, spatial index, etc.)
instead of doing a full rebuild after every routing change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox

logger = logging.getLogger(__name__)


@dataclass
class ChangedArea:
    """Tracks modified regions of the board.

    When traces or vias are added, removed, or moved, the affected area
    is recorded here. Consumers (like the expansion graph builder) can
    check whether their cached state overlaps a changed area and rebuild
    only what's needed.

    Usage::

        changed = ChangedArea()
        changed.mark_changed(trace.bounding_box())
        ...
        if changed.overlaps(room.bbox):
            rebuild(room)
        changed.clear()
    """

    _regions: list[BoundingBox] = field(default_factory=list)
    _merged_bbox: BoundingBox | None = None

    def mark_changed(self, region: BoundingBox):
        """Record that a region of the board has been modified."""
        self._regions.append(region)
        if self._merged_bbox is None:
            self._merged_bbox = region
        else:
            self._merged_bbox = self._merged_bbox.union(region)

    def mark_point_changed(self, point: IntPoint, radius: int):
        """Record a change around a point (e.g., via insertion)."""
        bb = BoundingBox.from_center_and_radius(point, radius)
        self.mark_changed(bb)

    def mark_item_changed(self, item) -> None:
        """Record that an item was added/removed/modified."""
        self.mark_changed(item.bounding_box())

    @property
    def has_changes(self) -> bool:
        return len(self._regions) > 0

    @property
    def region_count(self) -> int:
        return len(self._regions)

    @property
    def merged_region(self) -> BoundingBox | None:
        """Bounding box enclosing all changed regions."""
        return self._merged_bbox

    def overlaps(self, bbox: BoundingBox) -> bool:
        """Check if any changed region overlaps the given bbox.

        Fast path: first check the merged bbox. If that doesn't overlap,
        no individual region can either.
        """
        if self._merged_bbox is None:
            return False
        if not self._merged_bbox.intersects(bbox):
            return False
        # Detailed check against each changed region
        for region in self._regions:
            if region.intersects(bbox):
                return True
        return False

    def clear(self):
        """Reset all tracked changes."""
        self._regions.clear()
        self._merged_bbox = None

    def get_affected_regions(self) -> list[BoundingBox]:
        """Get all individual changed regions."""
        return list(self._regions)

    def merge_regions(self, tolerance: int = 0) -> list[BoundingBox]:
        """Merge overlapping changed regions into larger blocks.

        Adjacent or overlapping regions are combined to reduce the number
        of rebuild operations. The tolerance expands each region before
        merge testing (useful for catching near-misses).
        """
        if not self._regions:
            return []

        # Simple greedy merge
        regions = [r.enlarge(tolerance) for r in self._regions]
        merged: list[BoundingBox] = [regions[0]]

        for r in regions[1:]:
            did_merge = False
            for i, m in enumerate(merged):
                if m.intersects(r):
                    merged[i] = m.union(r)
                    did_merge = True
                    break
            if not did_merge:
                merged.append(r)

        return merged

    def __str__(self) -> str:
        if not self.has_changes:
            return "ChangedArea(no changes)"
        return f"ChangedArea({len(self._regions)} regions, merged={self._merged_bbox})"
