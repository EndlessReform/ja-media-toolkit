"""Fixtures shared by anime-audio service tests."""

from __future__ import annotations

import json
from pathlib import Path


def manifest(
    *,
    anilist_id: int = 1,
    title: str = "Example",
    artifact_path: str = "S01E001.m4a",
    episode_key: str = "1",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "anime-audio-series",
        "series": {
            "anilist_id": anilist_id,
            "title_english": title,
            "title_native": "例",
            "title_romaji": title,
            "title_preferred": title,
            "description_html": None,
            "description_text": None,
            "format": "TV",
            "status": "FINISHED",
            "season": "SPRING",
            "season_year": 2026,
            "episode_count": 1,
            "typical_duration_minutes": 24,
            "start_date": "2026-04-01",
            "end_date": None,
            "genres": ["Drama"],
            "source": "ORIGINAL",
            "country_of_origin": "JP",
            "banner_url": None,
            "mal_id": 2,
            "site_url": f"https://anilist.co/anime/{anilist_id}",
            "upstream_updated_at": 1,
            "metadata_snapshot": {"title_english": title},
            "cover": None,
        },
        "profile": {
            "name": "portable-aac-v1",
            "container": "m4a",
            "codec": "aac",
            "bitrate_bps": 128000,
            "max_channels": 2,
            "sample_rate_hz": 48000,
        },
        "episodes": [
            {
                "episode_key": episode_key,
                "source": {
                    "relative_path": f"Episode {episode_key}.mkv",
                    "size_bytes": 100,
                    "mtime_ns": 1,
                    "global_stream_index": 1,
                    "audio_stream_ordinal": 0,
                    "audio_codec": "flac",
                    "audio_language": "jpn",
                },
                "artifact": {
                    "relative_path": artifact_path,
                    "size_bytes": 10,
                    "duration_ms": 1000,
                    "codec": "aac",
                    "bitrate_bps": 128000,
                    "channels": 2,
                    "sample_rate_hz": 48000,
                    "sha256": "abc",
                },
                "created_at": "2026-06-01T00:00:00+00:00",
            }
        ],
    }


def write_series(
    library: Path,
    *,
    directory: str = "anilist-1",
    payload: dict[str, object] | None = None,
    write_artifact: bool = True,
) -> Path:
    series = library / directory
    series.mkdir(parents=True, exist_ok=True)
    data = payload or manifest()
    if write_artifact:
        artifact = str(data["episodes"][0]["artifact"]["relative_path"])  # type: ignore[index]
        (series / artifact).write_bytes(b"audio-bytes")
    path = series / ".ja-media.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path
