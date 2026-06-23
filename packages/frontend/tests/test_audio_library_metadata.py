from __future__ import annotations

from unittest.mock import patch

import httpx

from ja_media_core.anilist_search import AnimeMetadata
from ja_media_frontend.audio_library.metadata import (
    description_to_plain_text,
    download_cover,
    normalize_anilist_metadata,
)


def test_normalize_anilist_metadata_handles_csv_shaped_values() -> None:
    metadata = AnimeMetadata(
        anilist_id=154587,
        fields={
            "title_english": "Frieren: Beyond Journey’s End",
            "title_native": "葬送のフリーレン",
            "title_romaji": "Sousou no Frieren",
            "description": "The adventure is over ...<br><br>(Source: Crunchyroll)",
            "seasonYear": 2023.0,
            "episodes": 28.0,
            "duration": 24.0,
            "startDate_year": 2023,
            "startDate_month": 9.0,
            "startDate_day": 29.0,
            "genres": '["Adventure", "Drama", "Fantasy"]',
            "idMal": 52991.0,
            "coverImage_extraLarge": "https://example.test/large.jpg",
            "coverImage_large": "https://example.test/small.jpg",
        },
    )

    result = normalize_anilist_metadata(metadata)

    assert result.title_preferred == "Frieren: Beyond Journey’s End"
    assert result.season_year == 2023
    assert result.episode_count == 28
    assert result.start_date.isoformat() == "2023-09-29"
    assert result.genres == ("Adventure", "Drama", "Fantasy")
    assert result.mal_id == 52991
    assert result.cover_url == "https://example.test/large.jpg"
    assert result.raw_snapshot["seasonYear"] == 2023.0


def test_description_to_plain_text_removes_markup_and_decodes_entities() -> None:
    assert (
        description_to_plain_text("<p>A &amp; B<br>C</p><i>Source</i>")
        == "A & B\nC\n\nSource"
    )


def test_download_cover_uses_httpx_without_environment_proxies(tmp_path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == "ja-media-toolkit/1"
        return httpx.Response(
            200,
            headers={"Content-Type": "image/jpeg"},
            content=b"cover-bytes",
        )

    destination = tmp_path / "cover.jpg"
    client = httpx.Client(transport=httpx.MockTransport(respond))
    with (
        patch(
            "ja_media_frontend.audio_library.metadata.httpx.Client",
            return_value=client,
        ) as factory,
        patch(
            "ja_media_frontend.audio_library.metadata._probe_cover",
            return_value=(1000, 1400, "mjpeg"),
        ),
    ):
        artifact = download_cover("http://covers.test/cover.jpg", destination)

    factory.assert_called_once_with(
        timeout=30,
        trust_env=False,
        follow_redirects=True,
    )
    assert destination.read_bytes() == b"cover-bytes"
    assert artifact.media_type == "image/jpeg"
    assert artifact.size_bytes == len(b"cover-bytes")
