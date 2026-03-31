"""
Board state history — snapshot-based undo/redo and save/restore.

BoardHistory maintains a stack of BoardHistoryEntry snapshots so that
routing operations can be undone. Each entry stores a shallow copy of
the board's item dict plus metadata about what changed.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kicad_autorouter.board.item import Item

if TYPE_CHECKING:
    from kicad_autorouter.board.board import RoutingBoard

logger = logging.getLogger(__name__)


@dataclass
class BoardHistoryEntry:
    """A snapshot of the board state at a point in time."""

    label: str                          # Human-readable description
    timestamp: float = 0.0             # When the snapshot was taken
    items_snapshot: dict[int, Item] = field(default_factory=dict)
    nets_snapshot: dict = field(default_factory=dict)

    @property
    def item_count(self) -> int:
        return len(self.items_snapshot)


class BoardHistory:
    """Undo/redo stack for board state.

    Usage::

        history = BoardHistory(board, max_entries=50)
        history.snapshot("before routing net 1")
        # ... do routing work ...
        history.undo()          # restore to previous state
        history.redo()          # re-apply the undone change
        history.save("checkpoint_1")
        history.restore("checkpoint_1")
    """

    def __init__(self, board: RoutingBoard, max_entries: int = 50):
        self.board = board
        self.max_entries = max_entries
        self._undo_stack: list[BoardHistoryEntry] = []
        self._redo_stack: list[BoardHistoryEntry] = []
        self._named_saves: dict[str, BoardHistoryEntry] = {}

    @property
    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    @property
    def undo_depth(self) -> int:
        return len(self._undo_stack)

    @property
    def redo_depth(self) -> int:
        return len(self._redo_stack)

    def snapshot(self, label: str = ""):
        """Take a snapshot of the current board state and push onto undo stack.

        Call this BEFORE making changes so that undo restores the previous state.
        """
        entry = self._capture(label)
        self._undo_stack.append(entry)

        # Clear redo stack (new action invalidates redo history)
        self._redo_stack.clear()

        # Trim if over capacity
        while len(self._undo_stack) > self.max_entries:
            self._undo_stack.pop(0)

        logger.debug("Snapshot '%s' (%d items)", label, entry.item_count)

    def undo(self) -> bool:
        """Restore the board to the most recent snapshot.

        The current state is pushed onto the redo stack before restoring.
        Returns False if nothing to undo.
        """
        if not self.can_undo:
            return False

        # Save current state for redo
        redo_entry = self._capture("redo")
        self._redo_stack.append(redo_entry)

        # Restore previous state
        entry = self._undo_stack.pop()
        self._restore(entry)

        logger.debug("Undo to '%s' (%d items)", entry.label, entry.item_count)
        return True

    def redo(self) -> bool:
        """Re-apply the most recently undone change.

        The current state is pushed onto the undo stack before restoring.
        Returns False if nothing to redo.
        """
        if not self.can_redo:
            return False

        # Save current state for undo
        undo_entry = self._capture("undo")
        self._undo_stack.append(undo_entry)

        # Restore redo state
        entry = self._redo_stack.pop()
        self._restore(entry)

        logger.debug("Redo to '%s' (%d items)", entry.label, entry.item_count)
        return True

    def save(self, name: str):
        """Save the current board state under a named checkpoint.

        Named saves are independent of the undo/redo stack.
        """
        entry = self._capture(name)
        self._named_saves[name] = entry
        logger.debug("Saved checkpoint '%s' (%d items)", name, entry.item_count)

    def restore(self, name: str) -> bool:
        """Restore board state from a named checkpoint.

        Returns False if the named save doesn't exist.
        Also pushes current state onto undo stack so the restore can be undone.
        """
        entry = self._named_saves.get(name)
        if entry is None:
            return False

        # Push current state for undo
        self.snapshot(f"before restore '{name}'")
        self._restore(entry)

        logger.debug("Restored checkpoint '%s' (%d items)", name, entry.item_count)
        return True

    def list_saves(self) -> list[str]:
        """List all named checkpoint names."""
        return list(self._named_saves.keys())

    def clear(self):
        """Clear all history (undo, redo, and named saves)."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._named_saves.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _capture(self, label: str) -> BoardHistoryEntry:
        """Capture a deep copy of the board's item state."""
        items_copy = {}
        for item_id, item in self.board._items.items():
            items_copy[item_id] = copy.deepcopy(item)

        nets_copy = copy.deepcopy(self.board.nets)

        return BoardHistoryEntry(
            label=label,
            timestamp=time.monotonic(),
            items_snapshot=items_copy,
            nets_snapshot=nets_copy,
        )

    def _restore(self, entry: BoardHistoryEntry):
        """Restore the board's item state from a snapshot."""
        self.board._items.clear()
        for item_id, item in entry.items_snapshot.items():
            self.board._items[item_id] = copy.deepcopy(item)

        self.board.nets = copy.deepcopy(entry.nets_snapshot)
