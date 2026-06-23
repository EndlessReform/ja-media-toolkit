from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from ja_media_core.audio_library import (
    PORTABLE_AAC_V1,
    AnimeAudioManifest,
    AnimeAudioSeriesMetadata,
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


def test_manifest_rejects_unknown_schema() -> None:
    payload = manifest_to_mapping(replace(_manifest(), schema_version=2))

    with pytest.raises(ValueError, match="unsupported"):
        manifest_from_mapping(payload)
