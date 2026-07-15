"""Application service shared by Dagster assets and the resolver CLI."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from ja_media_core.bronze import (
    BronzeCaptureManifest,
    BronzeManifestError,
    parse_bronze_manifest,
)

from ja_media_data.bronze_store import BronzeDocument, BronzeMarker, BronzeStore
from ja_media_data.episode_metadata import EpisodeMetadataProvider
from ja_media_data.episode_resolution import (
    EpisodeResolutionPlan,
    invalid_manifest_issue,
    overlap_issue,
    plan_episode_resolution,
)
from ja_media_data.ledger_types import CaptureObservation
from ja_media_data.models import BronzeCapture
from ja_media_data.repository import BindingConflictError, LedgerRepository


@dataclass(frozen=True)
class ResolutionResult:
    """One explainable batch/asset outcome suitable for logs and JSONL."""

    capture_id: str
    series_id: str
    stem: str
    classification: str
    reason: str
    locator: str | None
    issue_kind: str | None
    evidence: dict[str, Any]


def resolve_document(
    document: BronzeDocument,
    *,
    store: BronzeStore,
    metadata_provider: EpisodeMetadataProvider,
    repository: LedgerRepository | None = None,
    dagster_run_id: str | None = None,
) -> ResolutionResult:
    """Plan one capture and optionally commit its ledger effects."""

    try:
        manifest = parse_bronze_manifest(
            document.manifest,
            capture_id=document.marker.capture_id,
            manifest_key=document.marker.key,
        )
    except BronzeManifestError as error:
        return _handle_invalid_manifest(
            document,
            store=store,
            repository=repository,
            error=error,
        )
    metadata = metadata_provider.get(
        manifest.series.namespace, manifest.series.identifier
    )
    plan = plan_episode_resolution(
        manifest,
        input_data_version=document.marker.etag,
        metadata=metadata,
        dagster_run_id=dagster_run_id,
    )
    if repository is not None:
        _index_capture(repository, store, document, manifest)
        plan = _prepare_safe_supersession(repository, plan)
        plan = _apply_plan(repository, plan, document.marker.etag)
    return _result(manifest, plan)


def resolve_indexed_capture(
    capture_id: str,
    *,
    store: BronzeStore,
    metadata_provider: EpisodeMetadataProvider,
    repository: LedgerRepository,
    dagster_run_id: str | None,
) -> ResolutionResult:
    """Resolve one sensor-indexed capture from its Dagster partition key."""

    capture = repository.get_capture(capture_id)
    if capture is None:
        raise LookupError(f"capture {capture_id!r} is not indexed")
    manifest = store.read_manifest(
        capture.manifest_key, expected_etag=capture.manifest_etag
    )
    document = BronzeDocument(
        marker=_marker_from_capture(capture),
        manifest=manifest,
    )
    return resolve_document(
        document,
        store=store,
        metadata_provider=metadata_provider,
        repository=repository,
        dagster_run_id=dagster_run_id,
    )


def _index_capture(
    repository: LedgerRepository,
    store: BronzeStore,
    document: BronzeDocument,
    manifest: BronzeCaptureManifest,
) -> None:
    repository.observe_capture(
        CaptureObservation(
            capture_id=manifest.capture_id,
            series_namespace=manifest.series.namespace,
            series_id=manifest.series.identifier,
            manifest_bucket=store.bucket,
            manifest_key=document.marker.key,
            manifest_etag=document.marker.etag,
            manifest_schema_version=manifest.schema_version,
            observed_at=datetime.now(UTC),
        )
    )


def _apply_plan(
    repository: LedgerRepository,
    plan: EpisodeResolutionPlan,
    input_data_version: str,
) -> EpisodeResolutionPlan:
    for hint in plan.hints:
        repository.add_hint(hint)
    if plan.issue is not None:
        repository.record_issue(plan.issue)
        return plan
    assert plan.binding is not None
    try:
        repository.accept_binding(plan.binding)
        repository.resolve_open_issues(
            plan.binding.audio_capture_id,
            note=f"accepted by {plan.binding.recipe_version}",
        )
        return plan
    except BindingConflictError:
        issue = overlap_issue(
            plan,
            capture_id=plan.binding.audio_capture_id,
            input_data_version=input_data_version,
        )
        repository.record_issue(issue)
        return replace(
            plan,
            classification="quarantined",
            reason="locator_or_capture_already_bound",
            binding=None,
            issue=issue,
        )


def _prepare_safe_supersession(
    repository: LedgerRepository, plan: EpisodeResolutionPlan
) -> EpisodeResolutionPlan:
    binding = plan.binding
    if binding is None:
        return plan
    current = repository.get_current_binding_for_capture(binding.audio_capture_id)
    if current is None or current.binding_id == binding.binding_id:
        return plan
    same_locator = (
        current.namespace,
        current.series_id,
        current.episode,
    ) == (binding.namespace, binding.series_id, binding.episode)
    if not same_locator:
        return plan
    return replace(
        plan,
        binding=replace(binding, supersedes_binding_id=current.binding_id),
    )


def _result(
    manifest: BronzeCaptureManifest, plan: EpisodeResolutionPlan
) -> ResolutionResult:
    binding = plan.binding
    locator = (
        f"{binding.namespace}:{binding.series_id}:{binding.episode}"
        if binding is not None
        else None
    )
    return ResolutionResult(
        capture_id=manifest.capture_id,
        series_id=manifest.series.identifier,
        stem=manifest.stem,
        classification=plan.classification,
        reason=plan.reason,
        locator=locator,
        issue_kind=plan.issue.kind if plan.issue else None,
        evidence=plan.evidence,
    )


def _handle_invalid_manifest(
    document: BronzeDocument,
    *,
    store: BronzeStore,
    repository: LedgerRepository | None,
    error: BronzeManifestError,
) -> ResolutionResult:
    parts = PurePosixPath(document.marker.key).parts
    series_id = (
        parts[parts.index("metadata") - 1] if "metadata" in parts else "unknown"
    )
    evidence = {"error": str(error), "manifest_key": document.marker.key}
    if repository is not None:
        repository.observe_capture(
            CaptureObservation(
                capture_id=document.marker.capture_id,
                series_namespace="anilist" if series_id != "unknown" else "unknown",
                series_id=series_id,
                manifest_bucket=store.bucket,
                manifest_key=document.marker.key,
                manifest_etag=document.marker.etag,
                manifest_schema_version=1,
                observed_at=datetime.now(UTC),
            )
        )
        repository.record_issue(
            invalid_manifest_issue(
                capture_id=document.marker.capture_id,
                input_data_version=document.marker.etag,
                error=str(error),
                manifest_key=document.marker.key,
            )
        )
    return ResolutionResult(
        capture_id=document.marker.capture_id,
        series_id=series_id,
        stem=PurePosixPath(document.marker.key).stem,
        classification="quarantined",
        reason="invalid_manifest",
        locator=None,
        issue_kind="invalid",
        evidence=evidence,
    )
def _marker_from_capture(capture: BronzeCapture) -> BronzeMarker:
    return BronzeMarker(
        capture_id=capture.capture_id,
        key=capture.manifest_key,
        etag=capture.manifest_etag,
        size=0,
        last_modified="indexed",
    )
