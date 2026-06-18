from __future__ import annotations

import unittest
from unittest.mock import patch

from ja_media_core.anilist_search import AnimeMetadata, HttpAniListSearchClient
from ja_media_core.config import JaMediaConfig, ServicesConfig


class AniListSearchContractTest(unittest.TestCase):
    def test_config_root_url_resolves_to_gateway_anilist_search_route(self) -> None:
        config = JaMediaConfig(
            services=ServicesConfig(root_url="http://magi06-ja-media-toolkit")
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("ja_media_core.services.load_config", return_value=config),
        ):
            client = HttpAniListSearchClient()

        self.assertEqual(
            client.base_url,
            "http://magi06-ja-media-toolkit/api/v1/anilist",
        )
        self.assertEqual(
            client._url("/anime/395?fields=description"),
            "http://magi06-ja-media-toolkit/api/v1/anilist/anime/395?fields=description",
        )

    def test_parses_metadata_response_into_flexible_field_mapping(self) -> None:
        metadata = AnimeMetadata.from_mapping(
            {
                "anilist_id": 395,
                "title_romaji": "Ginga Eiyuu Densetsu",
                "characters": [
                    {
                        "role": "MAIN",
                        "node": {"name": {"native": "ラインハルト"}},
                    }
                ],
            }
        )

        self.assertEqual(metadata.anilist_id, 395)
        self.assertEqual(metadata.get("title_romaji"), "Ginga Eiyuu Densetsu")
        self.assertEqual(
            metadata.get("characters")[0]["node"]["name"]["native"],
            "ラインハルト",
        )

    def test_old_gateway_search_base_url_is_normalized_to_anilist_root(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANILIST_SEARCH_BASE_URL": "http://localhost:8080/api/v1/anilist/search"},
            clear=True,
        ):
            client = HttpAniListSearchClient()

        self.assertEqual(client.base_url, "http://localhost:8080/api/v1/anilist")
        self.assertEqual(
            client._url("/search?query=Death+Note"),
            "http://localhost:8080/api/v1/anilist/search?query=Death+Note",
        )

    def test_metadata_request_adds_comma_separated_fields(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANILIST_SEARCH_BASE_URL": "http://127.0.0.1:8000"},
            clear=True,
        ):
            client = HttpAniListSearchClient()

        with patch.object(
            client,
            "_get_json",
            return_value={"anilist_id": 395, "description": "Space opera."},
        ) as get_json:
            metadata = client.anime(
                395, fields=("description", "characters", "relations")
            )

        get_json.assert_called_once_with(
            "/anime/395?fields=description%2Ccharacters%2Crelations"
        )
        self.assertEqual(metadata.get("description"), "Space opera.")


if __name__ == "__main__":
    unittest.main()
