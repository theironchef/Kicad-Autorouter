"""
DRC violation types and result model.

Each violation type captures the specific information needed to locate
and describe a design rule failure. The DrcResult aggregates all
violations from a full board check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from kicad_autorouter.geometry.point import IntPoint


class ViolationType(Enum):
    """Categories of design rule violations."""

    # Clearance violations
    TRACE_TRACE_CLEARANCE = auto()
    TRACE_PAD_CLEARANCE = auto()
    TRACE_VIA_CLEARANCE = auto()
    VIA_VIA_CLEARANCE = auto()
    VIA_PAD_CLEARANCE = auto()
    PAD_PAD_CLEARANCE = auto()
    TRACE_OBSTACLE_CLEARANCE = auto()
    VIA_OBSTACLE_CLEARANCE = auto()

    # Hole clearance
    HOLE_CLEARANCE = auto()

    # Connectivity
    UNCONNECTED_ITEMS = auto()
    DISCONNECTED_NET_GROUP = auto()

    # Dangling / useless items
    DANGLING_TRACE = auto()
    SINGLE_LAYER_VIA = auto()

    # Length matching
    LENGTH_MISMATCH = auto()

    # Board edge
    BOARD_EDGE_CLEARANCE = auto()


class Severity(Enum):
    """How serious a violation is."""

    ERROR = auto()
    WARNING = auto()
    INFO = auto()


@dataclass(frozen=True)
class DrcViolation:
    """A single design rule violation.

    Attributes:
        violation_type: Category of the violation.
        severity: Error, warning, or informational.
        message: Human-readable description.
        location: Position on the board where the violation occurs.
        layer_index: Copper layer where the violation occurs (-1 = any).
        item_ids: IDs of the board items involved.
        net_codes: Net codes of the items involved.
        actual_value: The measured value that failed (e.g., actual clearance).
        required_value: The minimum required value (e.g., required clearance).
    """

    violation_type: ViolationType
    severity: Severity
    message: str
    location: IntPoint = IntPoint(0, 0)
    layer_index: int = -1
    item_ids: tuple[int, ...] = ()
    net_codes: tuple[int, ...] = ()
    actual_value: float = 0.0
    required_value: float = 0.0

    def __str__(self) -> str:
        loc = f"({self.location.x / 1_000_000:.3f}mm, {self.location.y / 1_000_000:.3f}mm)"
        return f"[{self.severity.name}] {self.violation_type.name} at {loc}: {self.message}"


@dataclass
class DrcResult:
    """Aggregated results from a full DRC run.

    Attributes:
        violations: All violations found.
        board_items_checked: Number of board items examined.
        nets_checked: Number of nets examined.
        elapsed_ms: Time taken in milliseconds.
    """

    violations: list[DrcViolation] = field(default_factory=list)
    board_items_checked: int = 0
    nets_checked: int = 0
    elapsed_ms: float = 0.0

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.WARNING)

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    def violations_of_type(self, vtype: ViolationType) -> list[DrcViolation]:
        return [v for v in self.violations if v.violation_type == vtype]

    def deduplicate(self) -> DrcResult:
        """Remove duplicate violations (same type, same items, same location)."""
        seen: set[tuple] = set()
        unique: list[DrcViolation] = []
        for v in self.violations:
            key = (v.violation_type, v.item_ids, v.location.x, v.location.y, v.layer_index)
            if key not in seen:
                seen.add(key)
                unique.append(v)
        return DrcResult(
            violations=unique,
            board_items_checked=self.board_items_checked,
            nets_checked=self.nets_checked,
            elapsed_ms=self.elapsed_ms,
        )

    def __str__(self) -> str:
        return (
            f"DRC: {self.error_count} errors, {self.warning_count} warnings "
            f"({len(self.violations)} total violations, "
            f"{self.board_items_checked} items checked in {self.elapsed_ms:.0f}ms)"
        )
