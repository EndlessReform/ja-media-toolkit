"""Immutable canonical episode and subtitle input records."""

from dataclasses import dataclass
from datetime import datetime


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
    audio_object_bucket: str
    audio_object_key: str
    audio_stream_index: int
    audio_codec: str | None
    audio_declared_language: str | None
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
