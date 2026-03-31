"""
Clearance matrix for design rule checking.

The clearance matrix defines minimum spacing between different item types
and clearance classes. The autorouter uses this to ensure all generated
routes meet spacing requirements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class ClearanceType(IntEnum):
    """Types of items for clearance lookup."""
    TRACE = 0
    VIA = 1
    PAD = 2
    COPPER_AREA = 3
    BOARD_OUTLINE = 4


@dataclass
class ClearanceMatrix:
    """Matrix of minimum clearance values between clearance classes.

    Clearance classes group items that share spacing rules. The matrix
    stores the minimum distance required between any pair of classes.
    For PCB routing, clearances are typically defined per net class.
    """

    # class_count x class_count matrix of clearance values (nanometers)
    _matrix: list[list[int]] = field(default_factory=list)
    _class_names: list[str] = field(default_factory=list)
    _default_clearance: int = 200_000  # 0.2mm default

    def __post_init__(self):
        if not self._matrix:
            # Initialize with single default class
            self._class_names = ["Default"]
            self._matrix = [[self._default_clearance]]

    @property
    def class_count(self) -> int:
        return len(self._class_names)

    def add_class(self, name: str, default_clearance: int | None = None) -> int:
        """Add a new clearance class. Returns its index."""
        idx = len(self._class_names)
        self._class_names.append(name)
        clearance = default_clearance or self._default_clearance

        # Expand matrix
        for row in self._matrix:
            row.append(clearance)
        self._matrix.append([clearance] * (idx + 1))
        return idx

    def get_class_index(self, name: str) -> int:
        """Get the index of a clearance class by name."""
        try:
            return self._class_names.index(name)
        except ValueError:
            return 0  # Default class

    def set_clearance(self, class1: int, class2: int, clearance: int):
        """Set clearance between two classes (symmetric)."""
        self._matrix[class1][class2] = clearance
        self._matrix[class2][class1] = clearance

    def get_clearance(self, class1: int, class2: int) -> int:
        """Get minimum clearance between two clearance classes."""
        if class1 < len(self._matrix) and class2 < len(self._matrix[class1]):
            return self._matrix[class1][class2]
        return self._default_clearance

    def max_clearance(self) -> int:
        """Maximum clearance value in the matrix."""
        if not self._matrix:
            return self._default_clearance
        return max(max(row) for row in self._matrix)
