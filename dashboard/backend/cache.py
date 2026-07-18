"""Minimal in-memory cache with TTL. Good enough for a single-process
dashboard backend; swap for Redis if this needs to run multi-process."""
import time
from typing import Any, Callable, Optional


class TTLCache:
    def __init__(self):
        self._store: dict[str, tuple[float, Any]] = {}

    def get_or_set(self, key: str, ttl_seconds: int, compute_fn: Callable[[], Any]) -> Any:
        now = time.time()
        cached = self._store.get(key)
        if cached and (now - cached[0]) < ttl_seconds:
            return cached[1]
        value = compute_fn()
        self._store[key] = (now, value)
        return value

    def clear(self):
        self._store.clear()


cache = TTLCache()
