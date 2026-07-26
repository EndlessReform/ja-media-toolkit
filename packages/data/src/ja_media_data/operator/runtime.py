"""FastAPI-lifetime ownership for attached repositories and projection cache."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from queue import LifoQueue
from typing import Iterator

from ja_media_data.lakehouse.repository import (
    DuckLakeRepository,
    repository_from_settings,
)
from ja_media_data.operator.application import OperatorApplication
from ja_media_data.operator.cache import ProjectionCache
from ja_media_data.operator.campaigns import CampaignCatalog
from ja_media_data.orchestration.dagster.definitions import build_definitions
from ja_media_data.orchestration.dagster.gateway import DagsterGateway


class RepositoryPool:
    """A tiny pool of fully attached DuckLake/PostgreSQL repository clients."""

    def __init__(self, size: int = 2) -> None:
        if size < 1:
            raise ValueError("repository pool size must be positive")
        self._stack = ExitStack()
        self._available: LifoQueue[DuckLakeRepository] = LifoQueue(maxsize=size)
        try:
            for _ in range(size):
                repository = self._stack.enter_context(
                    repository_from_settings(ensure_schema=False)
                )
                self._available.put(repository)
        except Exception:
            self._stack.close()
            raise

    @contextmanager
    def borrow(self) -> Iterator[DuckLakeRepository]:
        """Give one request exclusive use of one non-thread-safe connection."""

        repository = self._available.get()
        try:
            yield repository
        finally:
            self._available.put(repository)

    def close(self) -> None:
        self._stack.close()


class OperatorRuntime:
    """Shared process state created and destroyed by FastAPI lifespan."""

    def __init__(self, *, pool_size: int = 2, cache_entries: int = 512) -> None:
        self.pool = RepositoryPool(pool_size)
        self.cache = ProjectionCache(cache_entries)
        self.gateway = DagsterGateway.from_settings()
        self.campaigns = CampaignCatalog(build_definitions())

    @contextmanager
    def application(self) -> Iterator[OperatorApplication]:
        """Create a cheap request application over one borrowed repository."""

        with self.pool.borrow() as repository:
            yield OperatorApplication(
                repository, self.gateway, campaigns=self.campaigns, cache=self.cache
            )

    def close(self) -> None:
        self.cache.clear()
        self.gateway.close()
        self.pool.close()
