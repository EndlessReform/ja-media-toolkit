from __future__ import annotations

import unittest
from unittest.mock import patch

from ja_media_core.config import JaMediaConfig, ServicesConfig
from ja_media_core.crosswalk import (
    CrosswalkBulkLookupResponse,
    CrosswalkLookupResponse,
    HttpAnimeCrosswalkClient,
    anime_list_lookup_rows,
    normalize_media_kind,
    normalize_source,
    resolve_path,
)


class CrosswalkContractTest(unittest.TestCase):
    def test_config_root_url_resolves_to_gateway_crosswalk_route(self) -> None:
        config = JaMediaConfig(
            services=ServicesConfig(root_url="http://magi06-ja-media-toolkit")
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("ja_media_core.services.load_config", return_value=config),
        ):
            client = HttpAnimeCrosswalkClient()

        self.assertEqual(
            client.base_url,
            "http://magi06-ja-media-toolkit/api/v1/crosswalk",
        )
        self.assertEqual(
            client._url(resolve_path("tvdb", 79099)),
            "http://magi06-ja-media-toolkit/api/v1/crosswalk/resolve/tvdb/79099",
        )

    def test_explicit_base_url_is_treated_as_exact_service_url(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANIME_CROSSWALK_BASE_URL": "http://127.0.0.1:8000"},
            clear=True,
        ):
            client = HttpAnimeCrosswalkClient()

        self.assertEqual(client.base_url, "http://127.0.0.1:8000")
        self.assertEqual(
            client._url(resolve_path("mal", 1)),
            "http://127.0.0.1:8000/resolve/mal/1",
        )

    def test_normalizes_sources_and_media_kinds(self) -> None:
        self.assertEqual(normalize_source("TheMovieDB"), "tmdb")
        self.assertEqual(normalize_source("myanimelist"), "mal")
        self.assertEqual(normalize_media_kind("series"), "tv")
        self.assertEqual(normalize_media_kind("film"), "movie")

    def test_builds_lookup_paths(self) -> None:
        self.assertEqual(resolve_path("tvdb", 79099), "/resolve/tvdb/79099")
        self.assertEqual(resolve_path("tmdb", 8864, "series"), "/resolve/tmdb/tv/8864")

    def test_parses_response_tuple_results(self) -> None:
        response = CrosswalkLookupResponse.from_mapping(
            {
                "source": "tvdb",
                "id": "79099",
                "media_kind": None,
                "count": 1,
                "results": [{"anidb_id": 5459}],
            }
        )

        self.assertEqual(response.external_id, "79099")
        self.assertEqual(response.results, ({"anidb_id": 5459},))

    def test_parses_bulk_response_ordered_results(self) -> None:
        response = CrosswalkBulkLookupResponse.from_mapping(
            {
                "count": 2,
                "results": [
                    {
                        "source": "tvdb",
                        "id": "79099",
                        "media_kind": "movie",
                        "count": 1,
                        "results": [{"anidb_id": 5459}],
                    },
                    {
                        "source": "mal",
                        "id": "999999",
                        "media_kind": None,
                        "count": 0,
                        "results": [],
                    },
                ],
            }
        )

        self.assertEqual(response.count, 2)
        self.assertEqual(response.results[0].external_id, "79099")
        self.assertEqual(response.results[1].count, 0)

    def test_builds_lookup_rows_from_anime_list_payload(self) -> None:
        rows = anime_list_lookup_rows(
            {
                "type": "MOVIE",
                "anidb_id": 5459,
                "tvdb_id": 79099,
                "themoviedb_id": {"movie": 128},
            },
            1,
        )

        self.assertIn(("tvdb", "79099", None, 1), rows)
        self.assertIn(("tvdb", "79099", "movie", 1), rows)
        self.assertIn(("tmdb", "128", "movie", 1), rows)


if __name__ == "__main__":
    unittest.main()
