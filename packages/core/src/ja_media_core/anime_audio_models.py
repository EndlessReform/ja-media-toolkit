"""Typed response models for the anime-audio LAN service."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnimeAudioArtifact:
    """One verified derived-audio artifact exposed by stable identity."""

    anilist_id: int
    episode_key: str
    profile: str
    filename: str
    size_bytes: int
    duration_ms: int
    codec: str
    bitrate_bps: int | None
    channels: int
    sample_rate_hz: int
    sha256: str | None
    created_at: str
    content_url: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioArtifact:
        return cls(**data)


@dataclass(frozen=True)
class AnimeAudioSubtitle:
    """One extracted embedded subtitle exposed by stable identity."""

    anilist_id: int
    episode_key: str
    subtitle_id: str
    language: str | None
    title: str | None
    codec: str
    default: bool
    filename: str
    size_bytes: int
    source_stream_index: int
    source_stream_ordinal: int
    sha256: str | None
    content_url: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioSubtitle:
        return cls(
            anilist_id=int(data["anilist_id"]),
            episode_key=str(data["episode_key"]),
            subtitle_id=str(data["subtitle_id"]),
            language=data.get("language"),
            title=data.get("title"),
            codec=str(data["codec"]),
            default=bool(data["default"]),
            filename=str(data["filename"]),
            size_bytes=int(data["size_bytes"]),
            source_stream_index=int(data["source_stream_index"]),
            source_stream_ordinal=int(data["source_stream_ordinal"]),
            sha256=data.get("sha256"),
            content_url=str(data["content_url"]),
        )


@dataclass(frozen=True)
class AnimeAudioSubtitleContent:
    """One subtitle payload returned by the bulk content endpoint."""

    subtitle: AnimeAudioSubtitle
    content: bytes

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioSubtitleContent:
        subtitle = AnimeAudioSubtitle.from_mapping(_required_object(data, "subtitle"))
        encoded = str(data["content_base64"])
        return cls(subtitle=subtitle, content=base64.b64decode(encoded))


@dataclass(frozen=True)
class AnimeAudioEpisode:
    """One indexed episode and its available artifacts."""

    anilist_id: int
    episode_key: str
    artifacts: tuple[AnimeAudioArtifact, ...]
    subtitles: tuple[AnimeAudioSubtitle, ...] = ()

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioEpisode:
        return cls(
            anilist_id=int(data["anilist_id"]),
            episode_key=str(data["episode_key"]),
            artifacts=tuple(
                AnimeAudioArtifact.from_mapping(item) for item in data.get("artifacts", ())
            ),
            subtitles=tuple(
                AnimeAudioSubtitle.from_mapping(item)
                for item in data.get("subtitles", ())
            ),
        )


@dataclass(frozen=True)
class AnimeAudioSeries:
    """Indexed series summary derived from its authoritative manifest."""

    anilist_id: int
    title: str
    title_english: str | None
    title_native: str | None
    title_romaji: str | None
    profile: str
    episode_count: int
    artifact_count: int
    subtitle_count: int = 0

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioSeries:
        return cls(
            anilist_id=int(data["anilist_id"]),
            title=str(data["title"]),
            title_english=data.get("title_english"),
            title_native=data.get("title_native"),
            title_romaji=data.get("title_romaji"),
            profile=str(data["profile"]),
            episode_count=int(data["episode_count"]),
            artifact_count=int(data["artifact_count"]),
            subtitle_count=int(data.get("subtitle_count", 0)),
        )


@dataclass(frozen=True)
class AnimeAudioInventorySeries:
    """One series entry in a complete inventory projection."""

    anilist_id: int
    title: str
    title_english: str | None
    title_native: str | None
    title_romaji: str | None
    profile: str
    episode_count: int
    artifact_count: int
    episode_keys: tuple[str, ...]
    artifact_profiles: tuple[str, ...]
    subtitle_count: int = 0
    subtitle_languages: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioInventorySeries:
        return cls(
            anilist_id=int(data["anilist_id"]),
            title=str(data["title"]),
            title_english=data.get("title_english"),
            title_native=data.get("title_native"),
            title_romaji=data.get("title_romaji"),
            profile=str(data["profile"]),
            episode_count=int(data["episode_count"]),
            artifact_count=int(data["artifact_count"]),
            episode_keys=tuple(str(key) for key in data.get("episode_keys", ())),
            artifact_profiles=tuple(
                str(profile) for profile in data.get("artifact_profiles", ())
            ),
            subtitle_count=int(data.get("subtitle_count", 0)),
            subtitle_languages=tuple(
                str(language) for language in data.get("subtitle_languages", ())
            ),
        )


@dataclass(frozen=True)
class AnimeAudioInventory:
    """Bounded top-level counts plus every indexed series."""

    series_count: int
    episode_count: int
    artifact_count: int
    subtitle_count: int
    series: tuple[AnimeAudioInventorySeries, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeAudioInventory:
        return cls(
            series_count=int(data["series_count"]),
            episode_count=int(data["episode_count"]),
            artifact_count=int(data["artifact_count"]),
            subtitle_count=int(data.get("subtitle_count", 0)),
            series=tuple(
                AnimeAudioInventorySeries.from_mapping(item)
                for item in data.get("series", ())
            ),
        )


def _required_object(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"Anime audio subtitle content {key} was not an object")
    return value
