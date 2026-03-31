"""Thread-safe unique ID generator for board items."""

import threading


class IdGenerator:
    """Generates unique integer IDs for board items."""

    def __init__(self, start: int = 1):
        self._counter = start
        self._lock = threading.Lock()

    def next_id(self) -> int:
        with self._lock:
            id_val = self._counter
            self._counter += 1
            return id_val

    @property
    def last_id(self) -> int:
        return self._counter - 1
