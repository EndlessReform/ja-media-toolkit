"""Small process-local cache keyed only by durable product identities."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable
from threading import RLock
from typing import TypeVar


T = TypeVar("T")


class ProjectionCache:
    """Thread-safe bounded LRU for disposable application DTOs."""

    def __init__(self, max_entries: int = 512) -> None:
        if max_entries < 1:
            raise ValueError("projection cache must contain at least one entry")
        self.max_entries = max_entries
        self._items: OrderedDict[Hashable, object] = OrderedDict()
        self._lock = RLock()

    def get_or_create(self, key: Hashable, factory: Callable[[], T]) -> T:
        """Return a keyed projection, constructing it once on a cache miss."""

        with self._lock:
            if key in self._items:
                value = self._items.pop(key)
                self._items[key] = value
                return value  # type: ignore[return-value]
        value = factory()
        with self._lock:
            self._items[key] = value
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)
        return value

    def clear(self) -> None:
        """Drop all disposable projections during application shutdown."""

        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
