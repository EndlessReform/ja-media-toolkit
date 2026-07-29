"""Pure per-document planning for the automatic resolution compiler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from ja_media_core.bronze import (
    BronzeCaptureManifest,
    BronzeManifestError,
    parse_bronze_manifest,
)

from ja_media_data.storage.bronze import BronzeDocument, BronzeStore
from ja_media_data.products.episode_resolution.metadata import EpisodeMetadataProvider
from ja_media_data.products.episode_resolution.policy import (
    EpisodeResolutionPlan,
    manifest_schema_validation_issue,
    plan_episode_resolution,
)
from ja_media_data.products.episode_resolution.reasons import (
    BRONZE_MANIFEST_FAILED_SCHEMA_VALIDATION,
)
from ja_media_data.products.episode_resolution.models import CaptureObservation


@dataclass(frozen=True)
class ResolutionResult:
    """One explainable capture outcome suitable for logs and JSONL."""

    capture_id: str
    series_id: str
    stem: str
    classification: str
    reason: str
    locator: str | None
    issue_kind: str | None
    evidence: dict[str, Any]


@dataclass(frozen=True)
class PlannedDocument:
    """One pure plan plus the capture header compiled from the same evidence."""

    plan: EpisodeResolutionPlan
    result: ResolutionResult
    observation: CaptureObservation
    manifest: BronzeCaptureManifest | None


def plan_document(
    document: BronzeDocument,
    *,
    store: BronzeStore,
    metadata_provider: EpisodeMetadataProvider,
    observed_at: datetime,
    run_source: str | None,
) -> PlannedDocument:
    """Parse one committed manifest and return claims without writing state."""

    try:
        manifest = parse_bronze_manifest(
            document.manifest,
            capture_id=document.marker.capture_id,
            manifest_key=document.marker.key,
        )
    except BronzeManifestError as error:
        return _invalid_plan(
            document,
            store=store,
            error=error,
            observed_at=observed_at,
            run_source=run_source,
        )
    plan = plan_episode_resolution(
        manifest,
        input_data_version=document.marker.etag,
        metadata=metadata_provider.get(
            manifest.series.namespace, manifest.series.identifier
        ),
        run_source=run_source,
    )
    return PlannedDocument(
        plan=plan,
        result=result_from_plan(manifest, plan),
        observation=_observation(document, store, manifest, observed_at),
        manifest=manifest,
    )


def result_from_plan(
    manifest: BronzeCaptureManifest, plan: EpisodeResolutionPlan
) -> ResolutionResult:
    """Project a domain plan into the stable CLI/report result contract."""

    proposal = plan.proposal
    return ResolutionResult(
        capture_id=manifest.capture_id,
        series_id=manifest.series.identifier,
        stem=manifest.stem,
        classification=plan.classification,
        reason=plan.reason,
        locator=(
            f"{proposal.namespace}:{proposal.series_id}:{proposal.episode}"
            if proposal is not None
            else None
        ),
        issue_kind=plan.issue.kind if plan.issue else None,
        evidence=plan.evidence,
    )


def _invalid_plan(
    document: BronzeDocument,
    *,
    store: BronzeStore,
    error: BronzeManifestError,
    observed_at: datetime,
    run_source: str | None,
) -> PlannedDocument:
    namespace, series_id = _series_hint(document.marker.key)
    issue = manifest_schema_validation_issue(
        capture_id=document.marker.capture_id,
        input_data_version=document.marker.etag,
        error=str(error),
        manifest_key=document.marker.key,
        run_source=run_source,
    )
    evidence = {"error": str(error), "manifest_key": document.marker.key}
    plan = EpisodeResolutionPlan(
        classification="quarantined",
        reason=BRONZE_MANIFEST_FAILED_SCHEMA_VALIDATION,
        hints=(),
        proposal=None,
        issue=issue,
        evidence=evidence,
    )
    return PlannedDocument(
        plan=plan,
        result=ResolutionResult(
            capture_id=document.marker.capture_id,
            series_id=series_id,
            stem=PurePosixPath(document.marker.key).stem,
            classification="quarantined",
            reason=BRONZE_MANIFEST_FAILED_SCHEMA_VALIDATION,
            locator=None,
            issue_kind="invalid",
            evidence=evidence,
        ),
        observation=CaptureObservation(
            capture_id=document.marker.capture_id,
            series_namespace=namespace,
            series_id=series_id,
            manifest_bucket=store.bucket,
            manifest_key=document.marker.key,
            manifest_etag=document.marker.etag,
            manifest_schema_version=1,
            manifest_modified_at=_manifest_modified_at(document),
            observed_at=observed_at,
        ),
        manifest=None,
    )


def _observation(
    document: BronzeDocument,
    store: BronzeStore,
    manifest: BronzeCaptureManifest,
    observed_at: datetime,
) -> CaptureObservation:
    return CaptureObservation(
        capture_id=manifest.capture_id,
        series_namespace=manifest.series.namespace,
        series_id=manifest.series.identifier,
        manifest_bucket=store.bucket,
        manifest_key=document.marker.key,
        manifest_etag=document.marker.etag,
        manifest_schema_version=manifest.schema_version,
        manifest_modified_at=_manifest_modified_at(document),
        observed_at=observed_at,
    )


def _manifest_modified_at(document: BronzeDocument) -> datetime:
    """Parse the commit marker timestamp used by canonical latest-wins policy."""

    return datetime.fromisoformat(document.marker.last_modified.replace("Z", "+00:00"))


def _series_hint(key: str) -> tuple[str, str]:
    parts = PurePosixPath(key).parts
    if "metadata" not in parts:
        return "unknown", "unknown"
    series_id = parts[parts.index("metadata") - 1]
    return ("anilist", series_id) if series_id.isdecimal() else ("unknown", series_id)
