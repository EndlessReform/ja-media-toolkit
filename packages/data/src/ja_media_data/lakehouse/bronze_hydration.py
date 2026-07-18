"""Compile immutable Garage manifest headers into the rebuildable bronze cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from ja_media_core.bronze import BronzeManifestError, parse_bronze_manifest

from ja_media_data.bronze_store import BronzeStore
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.resolution_types import CaptureObservation
from ja_media_data.resolution_fingerprints import fingerprint_observations


@dataclass(frozen=True)
class HydrationResult:
    """Bounded scan counts suitable for CLI and test assertions."""

    observed: int
    valid: int
    invalid: int
    written: bool


def hydrate_bronze_captures(
    store: BronzeStore,
    repository: DuckLakeRepository,
    *,
    limit: int,
    observed_at: datetime | None = None,
) -> HydrationResult:
    """Scan committed manifests and replace the normalized cache once."""

    if limit < 1:
        raise ValueError("limit must be positive")
    timestamp = observed_at or datetime.now(UTC)
    valid = 0
    invalid = 0
    observations: list[CaptureObservation] = []
    for document in store.scan_documents(limit=limit):
        try:
            manifest = parse_bronze_manifest(
                document.manifest,
                capture_id=document.marker.capture_id,
                manifest_key=document.marker.key,
            )
            namespace = manifest.series.namespace
            series_id = manifest.series.identifier
            schema_version = manifest.schema_version
            valid += 1
        except BronzeManifestError:
            namespace, series_id = _series_hint(document.marker.key)
            schema_version = 1
            invalid += 1
        observations.append(
            CaptureObservation(
                capture_id=document.marker.capture_id,
                series_namespace=namespace,
                series_id=series_id,
                manifest_bucket=store.bucket,
                manifest_key=document.marker.key,
                manifest_etag=document.marker.etag,
                manifest_schema_version=schema_version,
                manifest_modified_at=datetime.fromisoformat(
                    document.marker.last_modified.replace("Z", "+00:00")
                ),
                observed_at=timestamp,
            )
        )
    write = repository.replace_bronze_captures(
        observations,
        fingerprint_observations(observations),
        scope="corpus",
        run_id="scan-bronze",
    )
    return HydrationResult(
        observed=valid + invalid,
        valid=valid,
        invalid=invalid,
        written=write.written,
    )


def _series_hint(key: str) -> tuple[str, str]:
    parts = PurePosixPath(key).parts
    if "metadata" not in parts:
        return "unknown", "unknown"
    series_id = parts[parts.index("metadata") - 1]
    return ("anilist", series_id) if series_id.isdecimal() else ("unknown", series_id)
