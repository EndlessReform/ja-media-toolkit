"""Immutable records shared by episode-resolution compilation and storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


class BindingConflictError(ValueError):
    """A human override conflicts with an enforced PostgreSQL binding head."""


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
    run_source: str | None = None


@dataclass(frozen=True)
class AutomaticBinding:
    """One deterministic row in the replaceable automatic binding product."""

    binding_id: str
    namespace: str
    series_id: str
    episode: str
    audio_capture_id: str
    decision_method: str
    decision_evidence: dict[str, Any]
    input_data_version: str
    recipe_version: str
    run_source: str | None = None


@dataclass(frozen=True)
class ResolutionIssueClaim:
    """One deterministic, inspectable resolver quarantine record."""

    issue_id: str
    capture_id: str
    hint_id: str | None
    kind: str
    details: dict[str, Any]
    run_source: str | None = None


@dataclass(frozen=True)
class ResolutionBatch:
    """Complete automatic resolver product prepared before a DuckLake commit."""

    hints: tuple[HintClaim, ...]
    bindings: tuple[AutomaticBinding, ...]
    issues: tuple[ResolutionIssueClaim, ...]


@dataclass(frozen=True)
class BatchWriteResult:
    """Whether a fingerprint caused a replacement and its product row counts."""

    written: bool
    fingerprint: str
    hints: int
    bindings: int
    issues: int


@dataclass(frozen=True)
class ReplaceResult:
    """Whether a fingerprint caused one rebuildable table to be replaced."""

    written: bool
    fingerprint: str
    rows: int
