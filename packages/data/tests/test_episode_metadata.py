"""AniList episode-bound normalization tests."""

from ja_media_core.anilist_search import AnimeMetadata

from ja_media_data.episode_metadata import AniListEpisodeMetadataProvider


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
