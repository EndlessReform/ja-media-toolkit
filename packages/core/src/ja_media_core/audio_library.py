"""Serializable contracts for the derived anime audio library.

This module intentionally contains no filesystem, subprocess, HTTP, or prompt
behavior.  Models and runtimes will change; these durable records describe what
was selected and produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class AudioStreamProbe:
    """One audio stream reported by ffprobe."""

    global_index: int
    audio_ordinal: int
    codec: str
    language: str | None
    title: str | None
    channels: int | None
    sample_rate_hz: int | None
    default: bool


@dataclass(frozen=True)
class SourceMediaProbe:
    """Stable source fingerprint and audio-stream inventory."""

    path: Path
    duration_ms: int
    size_bytes: int
    mtime_ns: int
    audio_streams: tuple[AudioStreamProbe, ...]


@dataclass(frozen=True)
class AnimeAudioSeriesMetadata:
    """Normalized AniList metadata used by audio-library manifests."""

    anilist_id: int
    title_english: str | None
    title_native: str | None
    title_romaji: str | None
    title_preferred: str
    description_html: str | None
    description_text: str | None
    format: str | None
    status: str | None
    season: str | None
    season_year: int | None
    episode_count: int | None
    typical_duration_minutes: int | None
    start_date: date | None
    end_date: date | None
    genres: tuple[str, ...]
    source: str | None
    country_of_origin: str | None
    cover_url: str | None
    banner_url: str | None
    mal_id: int | None
    site_url: str | None
    upstream_updated_at: int | None
    raw_snapshot: Mapping[str, object] = field(repr=False)


@dataclass(frozen=True)
class EpisodeMapping:
    """One confirmed source episode and selected audio stream."""

    episode_key: str
    source: SourceMediaProbe
    stream: AudioStreamProbe

    @property
    def source_path(self) -> Path:
        return self.source.path


@dataclass(frozen=True)
class AudioProfile:
    """A versioned, reproducible audio conversion profile."""

    name: str
    container: str
    codec: str
    bitrate_bps: int
    max_channels: int
    sample_rate_hz: int


PORTABLE_AAC_V1 = AudioProfile(
    name="portable-aac-v1",
    container="m4a",
    codec="aac",
    bitrate_bps=128_000,
    max_channels=2,
    sample_rate_hz=48_000,
)


@dataclass(frozen=True)
class MaterializationPlan:
    """Complete, user-confirmed work plan with no hidden identity decisions."""

    source_root: Path
    destination_root: Path
    series: AnimeAudioSeriesMetadata
    mappings: tuple[EpisodeMapping, ...]
    profile: AudioProfile

    @property
    def series_directory(self) -> Path:
        return self.destination_root / f"anilist-{self.series.anilist_id}"


@dataclass(frozen=True)
class CoverArtifact:
    """Verified cover image published beside a series."""

    source_url: str
    path: str
    media_type: str
    width: int
    height: int
    size_bytes: int


@dataclass(frozen=True)
class ArtifactRecord:
    """Verified audio artifact facts captured after conversion."""

    relative_path: str
    size_bytes: int
    duration_ms: int
    codec: str
    bitrate_bps: int | None
    channels: int
    sample_rate_hz: int
    sha256: str | None = None


@dataclass(frozen=True)
class ManifestEpisode:
    """One completed episode entry in the canonical manifest."""

    episode_key: str
    source_relative_path: str
    source_size_bytes: int
    source_mtime_ns: int
    global_stream_index: int
    audio_stream_ordinal: int
    audio_codec: str
    audio_language: str | None
    artifact: ArtifactRecord
    created_at: str


@dataclass(frozen=True)
class AnimeAudioManifest:
    """Versioned, rebuildable source of truth for one derived series."""

    series: AnimeAudioSeriesMetadata
    profile: AudioProfile
    episodes: tuple[ManifestEpisode, ...] = ()
    cover: CoverArtifact | None = None
    schema_version: int = 1
    kind: str = "anime-audio-series"
