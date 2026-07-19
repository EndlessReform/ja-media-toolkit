"""AniList episode-bound normalization tests."""

import pytest

from ja_media_core.anilist_search import AnimeMetadata
from ja_media_core.http import ServiceHttpError

from ja_media_data.products.episode_resolution.metadata import AniListEpisodeMetadataProvider


class FakeClient:
    def anime(self, anilist_id: int, *, fields=None) -> AnimeMetadata:
        assert anilist_id == 10087
        assert fields == (
            "episodes",
            "format",
            "title_romaji",
            "title_english",
            "title_native",
            "synonyms",
        )
        return AnimeMetadata(
            anilist_id=anilist_id,
            fields={
                "episodes": 13.0,
                "format": "TV",
                "title_romaji": "Fate/Zero",
                "title_english": "Fate/Zero",
                "synonyms": ["F/Z"],
            },
        )


def test_integral_csv_float_becomes_episode_count() -> None:
    metadata = AniListEpisodeMetadataProvider(FakeClient()).get("anilist", "10087")

    assert metadata is not None
    assert metadata.episode_count == 13
    assert metadata.titles == ("Fate/Zero", "F/Z")


def test_missing_exact_anilist_id_has_no_independent_metadata() -> None:
    class MissingClient:
        def anime(self, anilist_id: int, *, fields=None) -> AnimeMetadata:
            raise ServiceHttpError("not found", status_code=404)

    metadata = AniListEpisodeMetadataProvider(MissingClient()).get(
        "anilist", "999999999"
    )

    assert metadata is None


def test_service_failure_names_the_series_that_blocked_compilation() -> None:
    class FailingClient:
        def anime(self, anilist_id: int, *, fields=None) -> AnimeMetadata:
            raise ServiceHttpError("unavailable", status_code=503)

    with pytest.raises(RuntimeError, match="anilist:12345"):
        AniListEpisodeMetadataProvider(FailingClient()).get("anilist", "12345")
