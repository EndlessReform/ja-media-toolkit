"""Run-scoped resources shared by the collection asset adapters."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit

import dagster as dg

from ja_media_data.lakehouse.repository import (
    DuckLakeRepository,
    repository_from_settings,
)
from ja_media_data.products.episode_resolution.metadata import (
    AniListEpisodeMetadataProvider,
    EpisodeMetadataProvider,
)
from ja_media_data.storage.bronze import (
    BronzeDocument,
    BronzeStore,
    bronze_store_from_settings,
)
from ja_media_data.settings import get_settings
from ja_media_data.workers.lid_handoff import CeleryLidHandoff
from ja_media_data.workers.marker_store import MarkerStore


@dataclass
class CanaryRuntime:
    """Read-only source clients for a bounded resolver evaluation."""

    store: BronzeStore
    metadata_provider: EpisodeMetadataProvider
    frozen_documents: Sequence[BronzeDocument] | None = None

    def selected_documents(self, limit: int) -> tuple[BronzeDocument, ...]:
        """Read no more than the requested number of source records."""

        if self.frozen_documents is not None:
            return tuple(self.frozen_documents[:limit])
        return tuple(self.store.scan_documents(limit=limit))


@dataclass
class ProductRuntime(CanaryRuntime):
    """Domain repository plus source clients held for one Dagster run.

    A run opens the DuckLake/PostgreSQL clients once. Assets share those
    clients because the collection campaign deliberately uses the in-process
    executor. Tests and bounded canaries can inject documents without changing
    asset identity or adding row-key partitions.
    """

    repository: DuckLakeRepository | None = None
    lid_handoff: CeleryLidHandoff | None = None
    _documents: tuple[BronzeDocument, ...] | None = field(default=None, init=False)

    def corpus_documents(self) -> tuple[BronzeDocument, ...]:
        """Return the complete declared corpus, loading it once per run."""

        if self._documents is None:
            source = (
                self.frozen_documents
                if self.frozen_documents is not None
                else self.store.scan_documents(limit=None)
            )
            self._documents = tuple(source)
        return self._documents

    def product_repository(self) -> DuckLakeRepository:
        """Return the repository with a narrow assertion for type checkers."""

        if self.repository is None:
            raise RuntimeError("product runtime has no repository")
        return self.repository


@contextmanager
def runtime_from_settings() -> Iterator[ProductRuntime]:
    """Open configured domain clients for one Dagster execution."""

    settings = get_settings()
    with repository_from_settings() as repository:
        yield ProductRuntime(
            store=bronze_store_from_settings(),
            metadata_provider=AniListEpisodeMetadataProvider(),
            repository=repository,
            lid_handoff=_lid_handoff(settings),
        )


@contextmanager
def canary_runtime_from_settings() -> Iterator[CanaryRuntime]:
    """Open only read-only source clients; do not attach DuckLake."""

    yield CanaryRuntime(
        store=bronze_store_from_settings(),
        metadata_provider=AniListEpisodeMetadataProvider(),
    )


@dg.resource
def product_runtime_resource(_context: dg.InitResourceContext):
    """Dagster resource whose lifetime amortizes clients across a local run."""

    with runtime_from_settings() as runtime:
        yield runtime


@dg.resource
def canary_runtime_resource(_context: dg.InitResourceContext):
    """Resource for bounded evaluation with no product repository."""

    with canary_runtime_from_settings() as runtime:
        yield runtime


def hardcoded_runtime_resource(runtime: ProductRuntime) -> dg.ResourceDefinition:
    """Inject an isolated runtime into in-process integration tests."""

    return dg.ResourceDefinition.hardcoded_resource(runtime)


def _lid_handoff(settings) -> CeleryLidHandoff:
    """Build the server half of the fixed DEV/local worker boundary."""

    broker_url = os.environ.get("JA_MEDIA_CELERY_BROKER_URL")
    if not broker_url:
        raise RuntimeError("JA_MEDIA_CELERY_BROKER_URL is not set")
    configured = settings.ducklake
    bucket = urlsplit(configured.data_path).netloc
    if not bucket:
        raise RuntimeError("DuckLake data_path must name the staging bucket")
    markers = MarkerStore(
        endpoint_url=configured.s3_endpoint_url or "",
        bucket=bucket,
        prefix=f"worker-staging/{settings.environment}",
        region=configured.s3_region,
        addressing_style="path",
        access_key_id=configured.s3_access_key_id or "",
        secret_access_key=configured.s3_secret_access_key or "",
    )
    return CeleryLidHandoff(broker_url=broker_url, markers=markers)
