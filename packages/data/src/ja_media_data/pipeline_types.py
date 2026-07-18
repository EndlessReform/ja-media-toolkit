"""Small immutable records crossing Phase D compilation boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AcceptedBinding:
    """One proposal admitted by the current automatic acceptance policy."""

    acceptance_id: str
    proposal_id: str
    namespace: str
    series_id: str
    episode: str
    audio_capture_id: str
    acceptance_method: str
    policy_version: str
    input_fingerprint: str


@dataclass(frozen=True)
class CanonicalEpisodeInput:
    """The accepted capture selected for one episode at compile time."""

    canonical_id: str
    namespace: str
    series_id: str
    episode: str
    audio_capture_id: str
    binding_id: str
    binding_source: str
    manifest_bucket: str
    manifest_key: str
    manifest_etag: str
    manifest_modified_at: datetime
    input_fingerprint: str


@dataclass(frozen=True)
class CanonicalSubtitleInput:
    """One subtitle object belonging to a canonical episode capture."""

    subtitle_input_id: str
    canonical_id: str
    namespace: str
    series_id: str
    episode: str
    audio_capture_id: str
    object_bucket: str
    object_key: str
    stream_index: int
    codec: str | None
    declared_language: str | None
    input_fingerprint: str


@dataclass(frozen=True)
class SubtitleLanguageResult:
    """Versioned language evidence for one canonical subtitle object."""

    subtitle_input_id: str
    namespace: str
    series_id: str
    episode: str
    audio_capture_id: str
    language: str
    reason: str
    script_metrics: dict[str, object]
    sampled_metrics: dict[str, object] | None
    input_fingerprint: str
    recipe_version: str


@dataclass(frozen=True)
class StageResult:
    """One stage outcome suitable for CLI output and future status projection."""

    target: str
    written: bool
    fingerprint: str
    rows: int
    run_id: str
    pipeline_run_id: str
    materialization_id: str
