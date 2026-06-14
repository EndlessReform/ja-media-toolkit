from __future__ import annotations

import unittest

from ja_media_core.crosswalk import (
    CrosswalkBulkLookupResponse,
    CrosswalkLookupResponse,
    anime_list_lookup_rows,
    normalize_media_kind,
    normalize_source,
    resolve_path,
)


class CrosswalkContractTest(unittest.TestCase):
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
