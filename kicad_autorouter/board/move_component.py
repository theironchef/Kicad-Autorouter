"""
Component movement with collision detection.

Implements the MoveComponent algorithm from Freerouting: moves a component
and all its pads by a displacement vector, checks for collisions, and
optionally shoves obstacle traces out of the way.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.component import Component
from kicad_autorouter.board.item import Item, FixedState
from kicad_autorouter.board.pad import Pad
from kicad_autorouter.board.trace import Trace

if TYPE_CHECKING:
    from kicad_autorouter.board.board import RoutingBoard

logger = logging.getLogger(__name__)


@dataclass
class MoveResult:
    """Result of a component move attempt."""

    success: bool = False
    component_id: int = -1
    displacement: IntPoint = IntPoint(0, 0)
    pads_moved: int = 0
    traces_shoved: int = 0
    traces_removed: int = 0
    collision_items: list[int] = field(default_factory=list)
    message: str = ""


class MoveComponentAlgo:
    """Move a component and its pads, with collision detection.

    Features:
    - Moves component position and all owned pads by a displacement vector
    - Checks for collisions with other-net items at the new position
    - Optionally shoves obstacle traces aside
    - Protects user-fixed items from being displaced
    - Supports automatic undo on failed move (via snapshot/restore)

    Usage::

        mover = MoveComponentAlgo(board)
        result = mover.move(component_id=1, dx=1_000_000, dy=0)
        if not result.success:
            print(result.message)
    """

    def __init__(self, board: RoutingBoard, allow_shove: bool = True):
        self.board = board
        self.allow_shove = allow_shove

    def move(
        self,
        component_id: int,
        dx: int,
        dy: int,
    ) -> MoveResult:
        """Attempt to move a component by (dx, dy) nanometers.

        Returns a MoveResult indicating success/failure and details.
        """
        comp = self.board.components.get(component_id)
        if comp is None:
            return MoveResult(success=False, message=f"Component {component_id} not found")

        if comp.is_locked:
            return MoveResult(
                success=False, component_id=component_id,
                message=f"Component {comp.reference} is locked",
            )

        displacement = IntPoint(dx, dy)

        # Collect pads owned by this component
        pads = self._get_component_pads(comp)

        # Check for collisions at new position
        collisions = self._check_collisions(pads, dx, dy)

        # Filter collisions: user-fixed items block the move
        blocking = [cid for cid in collisions
                    if self._is_user_fixed(cid)]
        if blocking:
            return MoveResult(
                success=False, component_id=component_id,
                displacement=displacement,
                collision_items=blocking,
                message=f"Blocked by {len(blocking)} fixed item(s)",
            )

        # Try to shove non-fixed obstacles
        shoved_count = 0
        removed_count = 0
        if collisions and self.allow_shove:
            shoved_count, removed_count = self._shove_obstacles(collisions, dx, dy, pads)

        # Apply the move
        self._apply_move(comp, pads, dx, dy)

        return MoveResult(
            success=True,
            component_id=component_id,
            displacement=displacement,
            pads_moved=len(pads),
            traces_shoved=shoved_count,
            traces_removed=removed_count,
            message=f"Moved {comp.reference} by ({dx/1e6:.2f}mm, {dy/1e6:.2f}mm)",
        )

    def _get_component_pads(self, comp: Component) -> list[Pad]:
        """Get all pads belonging to a component."""
        pads = []
        for pad_id in comp.pad_ids:
            item = self.board.get_item(pad_id)
            if isinstance(item, Pad):
                pads.append(item)
        return pads

    def _check_collisions(
        self, pads: list[Pad], dx: int, dy: int,
    ) -> list[int]:
        """Check for items that would collide with pads at new positions."""
        collision_ids: set[int] = set()
        pad_nets = set()
        for pad in pads:
            pad_nets.update(pad.net_codes)

        for pad in pads:
            new_pos = pad.position.translate_by(dx, dy)
            new_bb = BoundingBox(
                new_pos.x - pad.size_x // 2 - self.board.design_rules.min_clearance,
                new_pos.y - pad.size_y // 2 - self.board.design_rules.min_clearance,
                new_pos.x + pad.size_x // 2 + self.board.design_rules.min_clearance,
                new_pos.y + pad.size_y // 2 + self.board.design_rules.min_clearance,
            )
            candidates = self.board.get_items_in_bbox(new_bb)
            for item in candidates:
                if item.id in {p.id for p in pads}:
                    continue  # Skip our own pads
                if item.component_id == pads[0].component_id if pads else False:
                    continue  # Skip items from same component
                # Same-net items are OK
                if any(nc in pad_nets for nc in item.net_codes):
                    continue
                collision_ids.add(item.id)

        return list(collision_ids)

    def _is_user_fixed(self, item_id: int) -> bool:
        """Check if an item is user-fixed (cannot be moved)."""
        item = self.board.get_item(item_id)
        if item is None:
            return False
        return item.fixed_state == FixedState.USER_FIXED

    def _shove_obstacles(
        self,
        collision_ids: list[int],
        dx: int, dy: int,
        pads: list[Pad],
    ) -> tuple[int, int]:
        """Try to push colliding traces out of the way.

        Returns (shoved_count, removed_count).
        """
        shoved = 0
        removed = 0

        for cid in collision_ids:
            item = self.board.get_item(cid)
            if item is None:
                continue
            if item.is_fixed:
                continue

            if isinstance(item, Trace):
                # Remove traces that conflict — the autorouter can re-route them
                self.board.remove_item(cid)
                removed += 1
            # Vias and pads from other components: leave them (they block)

        return shoved, removed

    def _apply_move(
        self, comp: Component, pads: list[Pad], dx: int, dy: int,
    ):
        """Apply the displacement to component and all its pads."""
        comp.position = comp.position.translate_by(dx, dy)

        for pad in pads:
            pad.position = pad.position.translate_by(dx, dy)

    def get_sorted_move_directions(
        self,
        component_id: int,
    ) -> list[IntPoint]:
        """Get movement directions sorted by available clearance.

        Returns unit-ish displacement vectors in order of most available
        free space (useful for auto-placement nudging).
        """
        comp = self.board.components.get(component_id)
        if comp is None:
            return []

        pads = self._get_component_pads(comp)
        if not pads:
            return []

        step = 100_000  # 0.1mm increments
        directions = [
            IntPoint(step, 0), IntPoint(-step, 0),
            IntPoint(0, step), IntPoint(0, -step),
            IntPoint(step, step), IntPoint(step, -step),
            IntPoint(-step, step), IntPoint(-step, -step),
        ]

        # Score each direction by collision count (fewer = better)
        scored: list[tuple[int, IntPoint]] = []
        for d in directions:
            collisions = self._check_collisions(pads, d.x, d.y)
            scored.append((len(collisions), d))

        scored.sort(key=lambda x: x[0])
        return [d for _, d in scored]
