"""Immutable subtitle language-identification records."""

from dataclasses import dataclass


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
