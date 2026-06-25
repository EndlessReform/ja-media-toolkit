from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class ExactIdSingleFlight:
    """Coalesce concurrent exact-ID fallback fetches in one process.

    The first request for an absent/expired AniList ID creates the outbound
    task. Later requests for the same ID await that task instead of starting
    their own GraphQL calls. Different IDs still run independently.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[int, asyncio.Task[T]] = {}
        self.coalesced_waits = 0

    async def run(
        self,
        anilist_id: int,
        factory: Callable[[], Awaitable[T]],
    ) -> T:
        created = False
        async with self._lock:
            task = self._tasks.get(anilist_id)
            if task is None:
                task = asyncio.create_task(factory())
                self._tasks[anilist_id] = task
                created = True
            else:
                self.coalesced_waits += 1

        try:
            return await task
        finally:
            if created:
                async with self._lock:
                    if self._tasks.get(anilist_id) is task:
                        del self._tasks[anilist_id]

    async def inflight_count(self) -> int:
        async with self._lock:
            return len(self._tasks)
