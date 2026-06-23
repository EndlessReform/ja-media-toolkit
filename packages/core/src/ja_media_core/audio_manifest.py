"""Serialization for the durable derived-anime-audio manifest contract."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any, Mapping

from ja_media_core.audio_library import (
    AnimeAudioManifest,
    AnimeAudioSeriesMetadata,
    ArtifactRecord,
    AudioProfile,
    CoverArtifact,
    ManifestEpisode,
)

SCHEMA_VERSION = 1
MANIFEST_KIND = "anime-audio-series"


def manifest_from_mapping(payload: Mapping[str, Any]) -> AnimeAudioManifest:
    """Validate and decode one schema-version-1 manifest mapping."""

    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("kind") != MANIFEST_KIND:
        raise ValueError("unsupported anime audio manifest")

    raw_series = payload.get("series")
    raw_profile = payload.get("profile")
    if not isinstance(raw_series, Mapping) or not isinstance(raw_profile, Mapping):
        raise ValueError("anime audio manifest requires series and profile objects")

    series_payload = dict(raw_series)
    cover_payload = series_payload.pop("cover", None)
    snapshot = series_payload.pop("metadata_snapshot", {})
    if cover_payload is not None and not isinstance(cover_payload, Mapping):
        raise ValueError("manifest series cover must be an object or null")
    if not isinstance(snapshot, Mapping):
        raise ValueError("manifest metadata_snapshot must be an object")

    series = AnimeAudioSeriesMetadata(
        **{
            **series_payload,
            "start_date": _optional_date(series_payload.get("start_date")),
            "end_date": _optional_date(series_payload.get("end_date")),
            "genres": tuple(series_payload.get("genres") or ()),
            "cover_url": cover_payload.get("source_url") if cover_payload else None,
            "raw_snapshot": dict(snapshot),
        }
    )
    raw_episodes = payload.get("episodes", ())
    if not isinstance(raw_episodes, list):
        raise ValueError("manifest episodes must be a list")
    return AnimeAudioManifest(
        series=series,
        profile=AudioProfile(**dict(raw_profile)),
        episodes=tuple(_episode_from_mapping(item) for item in raw_episodes),
        cover=CoverArtifact(**dict(cover_payload)) if cover_payload else None,
    )


def manifest_to_mapping(manifest: AnimeAudioManifest) -> dict[str, object]:
    """Convert a typed manifest to its stable on-disk representation."""

    series = asdict(manifest.series)
    series["start_date"] = _date_text(manifest.series.start_date)
    series["end_date"] = _date_text(manifest.series.end_date)
    series["genres"] = list(manifest.series.genres)
    series["metadata_snapshot"] = dict(manifest.series.raw_snapshot)
    series.pop("raw_snapshot")
    series.pop("cover_url")
    series["cover"] = asdict(manifest.cover) if manifest.cover else None
    return {
        "schema_version": manifest.schema_version,
        "kind": manifest.kind,
        "series": series,
        "profile": asdict(manifest.profile),
        "episodes": [_episode_to_mapping(item) for item in manifest.episodes],
    }


def _episode_to_mapping(episode: ManifestEpisode) -> dict[str, object]:
    return {
        "episode_key": episode.episode_key,
        "source": {
            "relative_path": episode.source_relative_path,
            "size_bytes": episode.source_size_bytes,
            "mtime_ns": episode.source_mtime_ns,
            "global_stream_index": episode.global_stream_index,
            "audio_stream_ordinal": episode.audio_stream_ordinal,
            "audio_codec": episode.audio_codec,
            "audio_language": episode.audio_language,
        },
        "artifact": asdict(episode.artifact),
        "created_at": episode.created_at,
    }


def _episode_from_mapping(payload: object) -> ManifestEpisode:
    if not isinstance(payload, Mapping):
        raise ValueError("manifest episode must be an object")
    source = payload.get("source")
    artifact = payload.get("artifact")
    if not isinstance(source, Mapping) or not isinstance(artifact, Mapping):
        raise ValueError("manifest episode requires source and artifact objects")
    return ManifestEpisode(
        episode_key=str(payload["episode_key"]),
        source_relative_path=str(source["relative_path"]),
        source_size_bytes=int(source["size_bytes"]),
        source_mtime_ns=int(source["mtime_ns"]),
        global_stream_index=int(source["global_stream_index"]),
        audio_stream_ordinal=int(source["audio_stream_ordinal"]),
        audio_codec=str(source["audio_codec"]),
        audio_language=source.get("audio_language"),
        artifact=ArtifactRecord(**dict(artifact)),
        created_at=str(payload["created_at"]),
    )


def _optional_date(value: object) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and value else None


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value else None
