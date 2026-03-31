"""
Priority queue for the maze search algorithm.

Uses a binary heap to efficiently retrieve the next expansion element
with the lowest cost. This is the core data structure driving the
A*-like maze search.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(order=True)
class PriorityEntry:
    """Entry in the priority queue with a cost and payload."""
    cost: float
    sequence: int = field(compare=True)  # Tiebreaker for equal costs
    item: Any = field(compare=False)
    valid: bool = field(default=True, compare=False)


class MazePriorityQueue:
    """Priority queue for maze search expansion.

    Supports efficient insert and extract-min operations. Uses lazy
    deletion: entries can be invalidated without removal from the heap.
    """

    def __init__(self):
        self._heap: list[PriorityEntry] = []
        self._counter = 0
        self._size = 0

    @property
    def size(self) -> int:
        return self._size

    @property
    def is_empty(self) -> bool:
        return self._size == 0

    def push(self, cost: float, item: Any) -> PriorityEntry:
        """Add an item with given cost. Returns the entry for later invalidation."""
        entry = PriorityEntry(cost=cost, sequence=self._counter, item=item)
        self._counter += 1
        heapq.heappush(self._heap, entry)
        self._size += 1
        return entry

    def pop(self) -> tuple[float, Any] | None:
        """Remove and return the lowest-cost item, or None if empty."""
        while self._heap:
            entry = heapq.heappop(self._heap)
            if entry.valid:
                self._size -= 1
                return (entry.cost, entry.item)
        return None

    def peek(self) -> tuple[float, Any] | None:
        """Look at the lowest-cost item without removing it."""
        while self._heap:
            if self._heap[0].valid:
                return (self._heap[0].cost, self._heap[0].item)
            heapq.heappop(self._heap)
        return None

    def invalidate(self, entry: PriorityEntry):
        """Mark an entry as invalid (lazy deletion)."""
        if entry.valid:
            entry.valid = False
            self._size -= 1

    def clear(self):
        """Remove all entries."""
        self._heap.clear()
        self._counter = 0
        self._size = 0
