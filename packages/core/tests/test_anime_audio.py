from __future__ import annotations

import unittest
from unittest.mock import patch

from ja_media_core.anime_audio import (
    AnimeAudioInventory,
    AnimeAudioInventorySeries,
    AnimeAudioNotFoundError,
    HttpAnimeAudioClient,
)
from ja_media_core.config import JaMediaConfig, ServicesConfig
from ja_media_core.http import ServiceHttpError


class AnimeAudioClientTest(unittest.TestCase):
    def test_config_root_resolves_gateway_route(self) -> None:
        config = JaMediaConfig(services=ServicesConfig(root_url="http://ja-media.local"))
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("ja_media_core.services.load_config", return_value=config),
        ):
            client = HttpAnimeAudioClient()

        self.assertEqual(client.base_url, "http://ja-media.local/api/v1/audio")

    def test_direct_environment_override_wins(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANIME_AUDIO_BASE_URL": "http://127.0.0.1:8123"},
            clear=True,
        ):
            client = HttpAnimeAudioClient()

        self.assertEqual(client.base_url, "http://127.0.0.1:8123")

    def test_parses_series_and_artifact_responses(self) -> None:
        client = HttpAnimeAudioClient("http://audio")
        with patch.object(
            client._http,
            "get_json",
            side_effect=[
                {
                    "anilist_id": 1,
                    "title": "Example",
                    "title_english": "Example",
                    "title_native": None,
                    "title_romaji": "Example",
                    "profile": "portable-aac-v1",
                    "episode_count": 1,
                    "artifact_count": 1,
                },
                {
                    "anilist_id": 1,
                    "episode_key": "1",
                    "profile": "portable-aac-v1",
                    "filename": "S01E001.m4a",
                    "size_bytes": 10,
                    "duration_ms": 1000,
                    "codec": "aac",
                    "bitrate_bps": 128000,
                    "channels": 2,
                    "sample_rate_hz": 48000,
                    "sha256": "abc",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "content_url": "/content",
                },
            ],
        ):
            series = client.series(1)
            artifact = client.artifact(1, "1")

        self.assertEqual(series.title, "Example")
        self.assertEqual(artifact.filename, "S01E001.m4a")

    def test_content_path_encodes_identity_components(self) -> None:
        client = HttpAnimeAudioClient("http://audio")
        with patch.object(client._http, "get_bytes", return_value=b"audio") as get_bytes:
            content = client.content(1, "SP 1", profile="portable/aac")

        self.assertEqual(content, b"audio")
        get_bytes.assert_called_once_with(
            "/series/1/episodes/SP%201/artifacts/portable%2Faac/content"
        )

    def test_service_errors_remain_meaningful(self) -> None:
        client = HttpAnimeAudioClient("http://audio")
        with (
            patch.object(
                client._http,
                "get_json",
                side_effect=ServiceHttpError(
                    "Anime audio request failed: 404 missing",
                    status_code=404,
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "404 missing"),
        ):
            client.series(999)

    def test_missing_artifact_has_typed_error(self) -> None:
        client = HttpAnimeAudioClient("http://audio")
        with (
            patch.object(
                client._http,
                "get_json",
                side_effect=ServiceHttpError(
                    "Anime audio request failed: 404 missing",
                    status_code=404,
                ),
            ),
            self.assertRaises(AnimeAudioNotFoundError),
        ):
            client.artifact(1, "SP 1")

    def test_inventory_parses_complete_projection(self) -> None:
        client = HttpAnimeAudioClient("http://audio")
        payload = {
            "series_count": 2,
            "episode_count": 3,
            "artifact_count": 4,
            "series": [
                {
                    "anilist_id": 2,
                    "title": "Alpha",
                    "title_english": "Alpha",
                    "title_native": None,
                    "title_romaji": "Alpha",
                    "profile": "portable-aac-v1",
                    "episode_count": 2,
                    "artifact_count": 2,
                    "episode_keys": ["1", "10"],
                    "artifact_profiles": ["portable-aac-v1"],
                },
                {
                    "anilist_id": 10,
                    "title": "Beta",
                    "title_english": None,
                    "title_native": "β",
                    "title_romaji": "Beta",
                    "profile": "portable-aac-v1",
                    "episode_count": 1,
                    "artifact_count": 2,
                    "episode_keys": ["1"],
                    "artifact_profiles": [
                        "portable-aac-v1",
                        "portable-opus-v1",
                    ],
                },
            ],
        }
        with patch.object(client._http, "get_json", return_value=payload):
            inventory = client.inventory()

        self.assertIsInstance(inventory, AnimeAudioInventory)
        self.assertEqual(inventory.series_count, 2)
        self.assertEqual(inventory.episode_count, 3)
        self.assertEqual(inventory.artifact_count, 4)
        self.assertEqual(len(inventory.series), 2)
        first = inventory.series[0]
        self.assertIsInstance(first, AnimeAudioInventorySeries)
        self.assertEqual(first.anilist_id, 2)
        self.assertEqual(first.episode_keys, ("1", "10"))
        self.assertEqual(first.artifact_profiles, ("portable-aac-v1",))
        second = inventory.series[1]
        self.assertEqual(second.artifact_profiles, ("portable-aac-v1", "portable-opus-v1"))

    def test_inventory_from_mapping_handles_empty_series(self) -> None:
        inventory = AnimeAudioInventory.from_mapping(
            {"series_count": 0, "episode_count": 0, "artifact_count": 0, "series": []}
        )
        self.assertEqual(inventory.series, ())


if __name__ == "__main__":
    unittest.main()
