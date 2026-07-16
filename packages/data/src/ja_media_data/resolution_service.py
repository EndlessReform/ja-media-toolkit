"""Batch orchestration for the automatic episode-resolution product."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, Sequence

from ja_media_data.bronze_store import BronzeDocument, BronzeStore
from ja_media_data.episode_metadata import EpisodeMetadataProvider
from ja_media_data.episode_resolution import overlap_issue
from ja_media_data.resolution_types import (
    BatchWriteResult,
    CaptureObservation,
    ReplaceResult,
    ResolutionBatch,
)
from ja_media_data.resolution_fingerprints import (
    fingerprint_observations,
    fingerprint_resolution,
)
from ja_media_data.resolution_planning import (
    PlannedDocument,
    ResolutionResult,
    plan_document,
    result_from_plan,
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
    ordered = sorted(documents, key=lambda item: (item.marker.key, item.marker.capture_id))
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
    planned = _quarantine_overlaps(planned)
    batch = ResolutionBatch(
        hints=tuple(hint for item in planned for hint in item.plan.hints),
        bindings=tuple(
            item.plan.binding for item in planned if item.plan.binding is not None
        ),
        issues=tuple(item.plan.issue for item in planned if item.plan.issue is not None),
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


def _quarantine_overlaps(items: list[PlannedDocument]) -> list[PlannedDocument]:
    locators: set[tuple[str, str, str]] = set()
    captures: set[str] = set()
    results: list[PlannedDocument] = []
    for item in items:
        binding = item.plan.binding
        if binding is None:
            results.append(item)
            continue
        locator = (binding.namespace, binding.series_id, binding.episode)
        if locator not in locators and binding.audio_capture_id not in captures:
            locators.add(locator)
            captures.add(binding.audio_capture_id)
            results.append(item)
            continue
        issue = overlap_issue(
            item.plan,
            capture_id=binding.audio_capture_id,
            input_data_version=binding.input_data_version,
        )
        plan = replace(
            item.plan,
            classification="quarantined",
            reason="locator_or_capture_already_bound",
            binding=None,
            issue=issue,
        )
        assert item.manifest is not None
        results.append(
            replace(item, plan=plan, result=result_from_plan(item.manifest, plan))
        )
    return results
