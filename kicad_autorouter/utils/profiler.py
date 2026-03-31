"""
Performance profiler for autorouting phases.

Tracks wall-clock time and item counts for each phase of the routing
pipeline. Results can be printed or exported for benchmarking.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PhaseMetrics:
    """Metrics for a single profiled phase."""

    name: str
    elapsed_ms: float = 0.0
    item_count: int = 0       # Items processed in this phase
    call_count: int = 0       # Number of times this phase was entered
    extra: dict = field(default_factory=dict)

    @property
    def avg_ms_per_call(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.elapsed_ms / self.call_count

    @property
    def avg_ms_per_item(self) -> float:
        if self.item_count == 0:
            return 0.0
        return self.elapsed_ms / self.item_count


class RoutingProfiler:
    """Profiler for the autorouting pipeline.

    Usage::

        profiler = RoutingProfiler()

        with profiler.phase("expansion_graph"):
            build_graph(...)

        with profiler.phase("maze_search", item_count=42):
            search(...)

        print(profiler.summary())
    """

    def __init__(self):
        self._phases: dict[str, PhaseMetrics] = {}
        self._start_time = time.monotonic()

    @contextmanager
    def phase(self, name: str, item_count: int = 0):
        """Context manager to time a named phase.

        Args:
            name: Phase name (e.g., "expansion_graph", "maze_search").
            item_count: Number of items being processed (for per-item stats).
        """
        t0 = time.monotonic()
        try:
            yield
        finally:
            elapsed = (time.monotonic() - t0) * 1000.0
            if name not in self._phases:
                self._phases[name] = PhaseMetrics(name=name)
            m = self._phases[name]
            m.elapsed_ms += elapsed
            m.item_count += item_count
            m.call_count += 1

    def record(self, name: str, elapsed_ms: float, item_count: int = 0, **extra):
        """Manually record a phase measurement."""
        if name not in self._phases:
            self._phases[name] = PhaseMetrics(name=name)
        m = self._phases[name]
        m.elapsed_ms += elapsed_ms
        m.item_count += item_count
        m.call_count += 1
        m.extra.update(extra)

    @property
    def total_elapsed_ms(self) -> float:
        return (time.monotonic() - self._start_time) * 1000.0

    @property
    def phases(self) -> dict[str, PhaseMetrics]:
        return dict(self._phases)

    def get_phase(self, name: str) -> PhaseMetrics | None:
        return self._phases.get(name)

    def summary(self) -> str:
        """Human-readable summary of all phases."""
        lines = ["Routing Profile:"]
        lines.append(f"  Total: {self.total_elapsed_ms:.1f}ms")
        lines.append("")

        # Sort by elapsed time (longest first)
        sorted_phases = sorted(
            self._phases.values(),
            key=lambda m: m.elapsed_ms,
            reverse=True,
        )

        total = self.total_elapsed_ms or 1.0
        for m in sorted_phases:
            pct = (m.elapsed_ms / total) * 100
            line = f"  {m.name:30s} {m.elapsed_ms:8.1f}ms  ({pct:5.1f}%)"
            if m.call_count > 1:
                line += f"  [{m.call_count} calls, {m.avg_ms_per_call:.1f}ms/call]"
            if m.item_count > 0:
                line += f"  [{m.item_count} items, {m.avg_ms_per_item:.2f}ms/item]"
            lines.append(line)

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Export as dict for JSON serialization or benchmarking."""
        return {
            "total_ms": self.total_elapsed_ms,
            "phases": {
                name: {
                    "elapsed_ms": m.elapsed_ms,
                    "item_count": m.item_count,
                    "call_count": m.call_count,
                    "avg_ms_per_call": m.avg_ms_per_call,
                    "avg_ms_per_item": m.avg_ms_per_item,
                    **m.extra,
                }
                for name, m in self._phases.items()
            },
        }

    def reset(self):
        """Clear all recorded phases."""
        self._phases.clear()
        self._start_time = time.monotonic()


@dataclass
class BenchmarkTarget:
    """Defines a timing target for a phase, used by the benchmark suite."""

    phase_name: str
    max_ms: float           # Maximum acceptable time in ms
    max_ms_per_item: float = 0.0  # Maximum per-item time (0 = don't check)

    def check(self, metrics: PhaseMetrics) -> bool:
        """Returns True if metrics meet the target."""
        if metrics.elapsed_ms > self.max_ms:
            return False
        if self.max_ms_per_item > 0 and metrics.avg_ms_per_item > self.max_ms_per_item:
            return False
        return True


def check_benchmarks(
    profiler: RoutingProfiler,
    targets: list[BenchmarkTarget],
) -> list[str]:
    """Check profiled results against benchmark targets.

    Returns a list of failure messages (empty = all passed).
    """
    failures: list[str] = []
    for target in targets:
        m = profiler.get_phase(target.phase_name)
        if m is None:
            failures.append(f"Phase '{target.phase_name}' not found in profiler")
            continue
        if not target.check(m):
            failures.append(
                f"Phase '{target.phase_name}': {m.elapsed_ms:.1f}ms "
                f"exceeds target {target.max_ms:.1f}ms"
            )
            if target.max_ms_per_item > 0:
                failures.append(
                    f"  per-item: {m.avg_ms_per_item:.2f}ms "
                    f"exceeds target {target.max_ms_per_item:.2f}ms"
                )
    return failures
