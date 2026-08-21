from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from ja_media_core.audio_library import (
    PORTABLE_AAC_V1,
    AnimeAudioManifest,
    AnimeAudioSeriesMetadata,
    SubtitleArtifactRecord,
)
from ja_media_core.audio_manifest import manifest_from_mapping, manifest_to_mapping


def _manifest() -> AnimeAudioManifest:
    return AnimeAudioManifest(
        series=AnimeAudioSeriesMetadata(
            anilist_id=1,
            title_english="Example",
            title_native="例",
            title_romaji="Example",
            title_preferred="Example",
            description_html=None,
            description_text=None,
            format="TV",
            status="FINISHED",
            season="SPRING",
            season_year=2026,
            episode_count=1,
            typical_duration_minutes=24,
            start_date=date(2026, 4, 1),
            end_date=None,
            genres=("Drama",),
            source="ORIGINAL",
            country_of_origin="JP",
            cover_url=None,
            banner_url=None,
            mal_id=2,
            site_url="https://anilist.co/anime/1",
            upstream_updated_at=1,
            raw_snapshot={"title_english": "Example"},
        ),
        profile=PORTABLE_AAC_V1,
    )


def test_manifest_mapping_round_trip() -> None:
    manifest = _manifest()
    payload = manifest_to_mapping(manifest)

    restored = manifest_from_mapping(payload)

    assert restored == manifest


def test_manifest_round_trip_preserves_subtitle_artifacts() -> None:
    manifest = _manifest()
    episode = manifest_from_mapping(
        {
            **manifest_to_mapping(manifest),
            "episodes": [
                {
                    "episode_key": "1",
                    "source": {
                        "relative_path": "Episode 01.mkv",
                        "size_bytes": 100,
                        "mtime_ns": 1,
                        "global_stream_index": 1,
                        "audio_stream_ordinal": 0,
                        "audio_codec": "flac",
                        "audio_language": "jpn",
                    },
                    "artifact": {
                        "relative_path": "S01E001.m4a",
                        "size_bytes": 10,
                        "duration_ms": 1000,
                        "codec": "aac",
                        "bitrate_bps": 128000,
                        "channels": 2,
                        "sample_rate_hz": 48000,
                        "sha256": "abc",
                    },
                    "subtitles": [
                        {
                            "subtitle_id": "stream-3",
                            "relative_path": "_subs/S01E001.stream-3.eng.srt",
                            "size_bytes": 20,
                            "codec": "ass",
                            "language": "eng",
                            "title": "English",
                            "default": True,
                            "source_stream_index": 3,
                            "source_stream_ordinal": 0,
                            "sha256": "def",
                        }
                    ],
                    "created_at": "2026-06-01T00:00:00Z",
                }
            ],
        }
    ).episodes[0]

    assert episode.subtitles == (
        SubtitleArtifactRecord(
            "stream-3",
            "_subs/S01E001.stream-3.eng.srt",
            20,
            "ass",
            "eng",
            "English",
            True,
            3,
            0,
            "def",
        ),
    )


def test_manifest_rejects_unknown_schema() -> None:
    payload = manifest_to_mapping(replace(_manifest(), schema_version=2))

    with pytest.raises(ValueError, match="unsupported"):
        manifest_from_mapping(payload)
