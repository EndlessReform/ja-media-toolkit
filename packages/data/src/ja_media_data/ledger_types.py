"""Small immutable commands passed into the episode-identity ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CaptureObservation:
    """Normalized header observed from one committed bronze manifest."""

    capture_id: str
    series_namespace: str
    series_id: str
    manifest_bucket: str
    manifest_key: str
    manifest_etag: str
    manifest_schema_version: int
    observed_at: datetime


@dataclass(frozen=True)
class HintClaim:
    """One immutable resolver claim and its exact provenance."""

    hint_id: str
    capture_id: str
    candidate_namespace: str
    candidate_series_id: str
    candidate_episode: str
    method: str
    confidence: float | None
    evidence: dict[str, Any]
    input_data_version: str
    recipe_version: str
    dagster_run_id: str | None = None


@dataclass(frozen=True)
class BindingDecision:
    """Accepted binding to commit with its current-head update."""

    binding_id: str
    namespace: str
    series_id: str
    episode: str
    audio_capture_id: str
    decision_method: str
    decision_evidence: dict[str, Any]
    input_data_version: str
    recipe_version: str
    supersedes_binding_id: str | None = None
    dagster_run_id: str | None = None


@dataclass(frozen=True)
class ResolutionIssueClaim:
    """One deterministic, inspectable resolver quarantine record."""

    issue_id: str
    capture_id: str
    hint_id: str | None
    kind: str
    details: dict[str, Any]
