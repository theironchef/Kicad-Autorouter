"""Time limit and cancellation support for autorouting."""

from __future__ import annotations

import time
import threading


class TimeLimit:
    """Tracks elapsed time and checks against a time budget.

    Used by the autorouter to abort if routing takes too long.
    Thread-safe: can be checked from worker threads.
    """

    def __init__(self, time_limit_seconds: float | None = None):
        self._start_time = time.monotonic()
        self._time_limit = time_limit_seconds
        self._cancelled = threading.Event()

    @property
    def elapsed(self) -> float:
        """Seconds elapsed since creation."""
        return time.monotonic() - self._start_time

    @property
    def remaining(self) -> float | None:
        """Seconds remaining, or None if no limit set."""
        if self._time_limit is None:
            return None
        return max(0.0, self._time_limit - self.elapsed)

    def is_expired(self) -> bool:
        """True if time limit exceeded or manually cancelled."""
        if self._cancelled.is_set():
            return True
        if self._time_limit is None:
            return False
        return self.elapsed >= self._time_limit

    def cancel(self):
        """Request cancellation."""
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def reset(self, time_limit_seconds: float | None = None):
        self._start_time = time.monotonic()
        self._time_limit = time_limit_seconds
        self._cancelled.clear()
