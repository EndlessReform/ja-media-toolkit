"""Collection compiler for the automatic episode-resolution product."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, Sequence

from ja_media_data.storage.bronze import BronzeDocument, BronzeStore
from ja_media_data.products.episode_resolution.metadata import EpisodeMetadataProvider
from ja_media_data.products.episode_resolution.models import (
    BatchWriteResult,
    CaptureObservation,
    ReplaceResult,
    ResolutionBatch,
)
from ja_media_data.products.episode_resolution.fingerprints import (
    fingerprint_observations,
    fingerprint_resolution,
)
from ja_media_data.products.episode_resolution.planning import (
    ResolutionResult,
    plan_document,
)


class BatchRepository(Protocol):
    """The two atomic replacement operations required by the resolver."""

    def replace_bronze_captures(
        self,
        observations: Sequence[CaptureObservation],
        fingerprint: str,
        *,
        scope: str,
        run_id: str | None,
    ) -> ReplaceResult: ...

    def replace_resolution_tables(
        self,
        batch: ResolutionBatch,
        fingerprint: str,
        *,
        scope: str,
        run_id: str | None,
    ) -> BatchWriteResult: ...


@dataclass(frozen=True)
class ResolutionBatchResult:
    """Compiled outcomes plus optional persistence results for both products."""

    results: tuple[ResolutionResult, ...]
    bronze_write: ReplaceResult | None
    resolution_write: BatchWriteResult | None


def resolve_document(
    document: BronzeDocument,
    *,
    store: BronzeStore,
    metadata_provider: EpisodeMetadataProvider,
    run_source: str | None = None,
) -> ResolutionResult:
    """Plan one document without performing persistence side effects."""

    return plan_document(
        document,
        store=store,
        metadata_provider=metadata_provider,
        observed_at=datetime.now(UTC),
        run_source=run_source,
    ).result


def resolve_batch(
    documents: Sequence[BronzeDocument],
    *,
    store: BronzeStore,
    metadata_provider: EpisodeMetadataProvider,
    repository: BatchRepository | None = None,
    scope: str = "corpus",
    run_source: str | None = None,
) -> ResolutionBatchResult:
    """Compile a deterministic corpus and write each changed product once."""

    timestamp = datetime.now(UTC)
    ordered = sorted(
        documents, key=lambda item: (item.marker.key, item.marker.capture_id)
    )
    planned = [
        plan_document(
            document,
            store=store,
            metadata_provider=metadata_provider,
            observed_at=timestamp,
            run_source=run_source,
        )
        for document in ordered
    ]
    batch = ResolutionBatch(
        hints=tuple(hint for item in planned for hint in item.plan.hints),
        proposals=tuple(
            item.plan.proposal for item in planned if item.plan.proposal is not None
        ),
        issues=tuple(
            item.plan.issue for item in planned if item.plan.issue is not None
        ),
    )
    observations = tuple(item.observation for item in planned)
    bronze_write = None
    resolution_write = None
    if repository is not None:
        bronze_write = repository.replace_bronze_captures(
            observations,
            fingerprint_observations(observations),
            scope=scope,
            run_id=run_source,
        )
        resolution_write = repository.replace_resolution_tables(
            batch,
            fingerprint_resolution(batch),
            scope=scope,
            run_id=run_source,
        )
    return ResolutionBatchResult(
        results=tuple(item.result for item in planned),
        bronze_write=bronze_write,
        resolution_write=resolution_write,
    )
