"""
Net operations — differential pairs, length matching, and routing priorities.

Provides algorithms for:
- Identifying differential pair nets by naming convention
- Computing trace lengths per net for length matching
- Assigning routing priority scores
- Generating length-matching violations
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kicad_autorouter.geometry.point import IntPoint

if TYPE_CHECKING:
    from kicad_autorouter.board.board import RoutingBoard
    from kicad_autorouter.board.net import Net, NetClass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Differential pair detection
# ---------------------------------------------------------------------------

# Common naming patterns for diff pairs: D+/D-, USB_P/USB_N, ETH_TX+/ETH_TX-
_DIFF_PAIR_PATTERNS = [
    (re.compile(r"^(.+)[_]?[Pp]$"), re.compile(r"^(.+)[_]?[Nn]$")),
    (re.compile(r"^(.+)\+$"), re.compile(r"^(.+)-$")),
    (re.compile(r"^(.+)_[Pp]$"), re.compile(r"^(.+)_[Nn]$")),
]


@dataclass
class DiffPair:
    """A matched differential pair of nets."""

    positive_net_code: int
    negative_net_code: int
    base_name: str               # Common base name (e.g., "USB_D")
    target_gap: int = 0          # Gap from net class (nm)
    target_width: int = 0        # Trace width from net class (nm)


def find_diff_pairs(board: RoutingBoard) -> list[DiffPair]:
    """Identify differential pairs by net name matching.

    Scans all nets and matches P/N or +/- naming patterns.
    """
    pairs: list[DiffPair] = []
    nets_by_name: dict[str, int] = {}
    for nc, net in board.nets.items():
        nets_by_name[net.name] = nc

    matched: set[int] = set()

    for name_p, code_p in nets_by_name.items():
        if code_p in matched:
            continue
        for pat_p, pat_n in _DIFF_PAIR_PATTERNS:
            m = pat_p.match(name_p)
            if not m:
                continue
            base = m.group(1)
            # Try to find matching negative net
            for name_n, code_n in nets_by_name.items():
                if code_n in matched or code_n == code_p:
                    continue
                mn = pat_n.match(name_n)
                if mn and mn.group(1) == base:
                    # Get diff-pair dimensions from net class
                    nc_obj = board.get_net_class_for_net(code_p)
                    pairs.append(DiffPair(
                        positive_net_code=code_p,
                        negative_net_code=code_n,
                        base_name=base,
                        target_gap=nc_obj.diff_pair_gap,
                        target_width=nc_obj.diff_pair_width,
                    ))
                    matched.add(code_p)
                    matched.add(code_n)
                    break
            if code_p in matched:
                break

    return pairs


# ---------------------------------------------------------------------------
# Length matching
# ---------------------------------------------------------------------------

@dataclass
class NetLength:
    """Total routed trace length for a net."""

    net_code: int
    net_name: str
    total_length_nm: float = 0.0
    segment_count: int = 0

    @property
    def total_length_mm(self) -> float:
        return self.total_length_nm / 1_000_000


@dataclass
class LengthMatchGroup:
    """A group of nets that should be length-matched."""

    name: str
    net_codes: list[int] = field(default_factory=list)
    tolerance_nm: float = 0.0    # Acceptable length variation

    def check(
        self, lengths: dict[int, NetLength],
    ) -> list[LengthViolation]:
        """Check if all nets in this group are within tolerance."""
        group_lengths = [
            lengths[nc] for nc in self.net_codes if nc in lengths
        ]
        if len(group_lengths) < 2:
            return []

        max_len = max(nl.total_length_nm for nl in group_lengths)
        min_len = min(nl.total_length_nm for nl in group_lengths)
        delta = max_len - min_len

        violations: list[LengthViolation] = []
        if delta > self.tolerance_nm:
            violations.append(LengthViolation(
                group_name=self.name,
                max_length_nm=max_len,
                min_length_nm=min_len,
                delta_nm=delta,
                tolerance_nm=self.tolerance_nm,
                net_codes=list(self.net_codes),
            ))

        return violations


@dataclass
class LengthViolation:
    """A length mismatch violation within a match group."""

    group_name: str
    max_length_nm: float
    min_length_nm: float
    delta_nm: float
    tolerance_nm: float
    net_codes: list[int] = field(default_factory=list)

    @property
    def delta_mm(self) -> float:
        return self.delta_nm / 1_000_000

    def __str__(self) -> str:
        return (
            f"Length mismatch in '{self.group_name}': "
            f"delta {self.delta_mm:.3f}mm > tolerance {self.tolerance_nm / 1e6:.3f}mm"
        )


def compute_net_lengths(board: RoutingBoard) -> dict[int, NetLength]:
    """Compute the total routed trace length for every net."""
    lengths: dict[int, NetLength] = {}

    for net_code, net in board.nets.items():
        if net_code <= 0:
            continue
        traces = board.get_traces_on_net(net_code)
        total = 0.0
        seg_count = 0
        for trace in traces:
            total += trace.total_length()
            seg_count += trace.segment_count

        lengths[net_code] = NetLength(
            net_code=net_code,
            net_name=net.name,
            total_length_nm=total,
            segment_count=seg_count,
        )

    return lengths


def check_diff_pair_lengths(
    board: RoutingBoard,
    tolerance_nm: float = 250_000,  # 0.25mm default
) -> list[LengthViolation]:
    """Check that differential pair nets have matched lengths."""
    pairs = find_diff_pairs(board)
    lengths = compute_net_lengths(board)
    violations: list[LengthViolation] = []

    for dp in pairs:
        group = LengthMatchGroup(
            name=dp.base_name,
            net_codes=[dp.positive_net_code, dp.negative_net_code],
            tolerance_nm=tolerance_nm,
        )
        violations.extend(group.check(lengths))

    return violations


# ---------------------------------------------------------------------------
# Routing priority
# ---------------------------------------------------------------------------

@dataclass
class NetPriority:
    """Routing priority for a net (lower number = routed first)."""

    net_code: int
    priority: int = 100          # Default priority
    reason: str = ""


def compute_net_priorities(board: RoutingBoard) -> list[NetPriority]:
    """Assign routing priorities to all nets.

    Priority rules (lower = sooner):
    1. Short connections first (fewer pads, shorter span)
    2. Signal nets before power/ground
    3. Nets in higher-priority net classes first
    4. Differential pairs routed together
    """
    priorities: list[NetPriority] = []
    diff_pairs = find_diff_pairs(board)
    dp_nets = set()
    for dp in diff_pairs:
        dp_nets.add(dp.positive_net_code)
        dp_nets.add(dp.negative_net_code)

    for net_code, net in board.nets.items():
        if net_code <= 0:
            continue

        pads = board.get_pads_on_net(net_code)
        if len(pads) < 2:
            continue

        # Base priority: Manhattan span (shorter = lower priority number)
        if len(pads) >= 2:
            xs = [p.position.x for p in pads]
            ys = [p.position.y for p in pads]
            span = (max(xs) - min(xs)) + (max(ys) - min(ys))
            # Normalize to 0-200 range (boards typically < 500mm)
            base = min(200, int(span / 1_000_000))
        else:
            base = 100

        # Power nets get deprioritized (+500)
        if net.is_power():
            base += 500

        # Diff pairs get boosted (-50)
        if net_code in dp_nets:
            base = max(0, base - 50)

        reason_parts = []
        if net.is_power():
            reason_parts.append("power")
        if net_code in dp_nets:
            reason_parts.append("diff-pair")
        reason_parts.append(f"span={span/1e6:.1f}mm" if len(pads) >= 2 else "single-pad")

        priorities.append(NetPriority(
            net_code=net_code,
            priority=base,
            reason=", ".join(reason_parts),
        ))

    priorities.sort(key=lambda p: p.priority)
    return priorities
