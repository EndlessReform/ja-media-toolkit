from __future__ import annotations

from datetime import date
from pathlib import Path

from ja_media_core.audio_library import (
    AnimeAudioManifest,
    AnimeAudioSeriesMetadata,
    PORTABLE_AAC_V1,
)
from ja_media_frontend.audio_library.manifest import (
    load_manifest,
    project_audiobookshelf_metadata,
    write_manifest_atomic,
)


def _manifest() -> AnimeAudioManifest:
    return AnimeAudioManifest(
        series=AnimeAudioSeriesMetadata(
            anilist_id=154587,
            title_english="Frieren",
            title_native="葬送のフリーレン",
            title_romaji="Sousou no Frieren",
            title_preferred="Frieren",
            description_html="Description<br>Source",
            description_text="Description\nSource",
            format="TV",
            status="FINISHED",
            season="FALL",
            season_year=2023,
            episode_count=28,
            typical_duration_minutes=24,
            start_date=date(2023, 9, 29),
            end_date=date(2024, 3, 22),
            genres=("Adventure", "Drama"),
            source="MANGA",
            country_of_origin="JP",
            cover_url="https://example.test/cover.jpg",
            banner_url=None,
            mal_id=52991,
            site_url="https://anilist.co/anime/154587",
            upstream_updated_at=1,
            raw_snapshot={"episodes": 28.0},
        ),
        profile=PORTABLE_AAC_V1,
    )


def test_manifest_round_trip_preserves_dates_genres_and_snapshot(tmp_path: Path) -> None:
    path = tmp_path / ".ja-media.json"

    write_manifest_atomic(path, _manifest())
    loaded = load_manifest(path)

    assert loaded.series.start_date == date(2023, 9, 29)
    assert loaded.series.genres == ("Adventure", "Drama")
    assert loaded.series.raw_snapshot == {"episodes": 28.0}


def test_audiobookshelf_projection_contains_identity_tags() -> None:
    projected = project_audiobookshelf_metadata(_manifest())

    assert projected["releaseDate"] == "2023-09-29"
    assert projected["genres"] == ["Anime", "Adventure", "Drama"]
    assert projected["tags"] == [
        "ja-media",
        "anime",
        "anilist:154587",
        "mal:52991",
    ]
