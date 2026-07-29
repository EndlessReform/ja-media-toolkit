"""Immutable records for the capture-audio eligibility product."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CaptureAudioEligibility:
    """One complete decision about whether a capture can supply canonical audio."""

    capture_id: str
    manifest_bucket: str
    manifest_key: str
    manifest_etag: str
    manifest_schema_version: int | None
    status: str
    reason: str
    selected_audio_object_bucket: str | None
    selected_audio_object_key: str | None
    selected_audio_stream_index: int | None
    selected_audio_codec: str | None
    selected_audio_declared_language: str | None
    available_audio_tracks: str
    input_fingerprint: str
