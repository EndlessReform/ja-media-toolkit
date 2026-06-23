from __future__ import annotations

import unittest
from unittest.mock import patch

from ja_media_core.anime_audio import HttpAnimeAudioClient
from ja_media_core.config import JaMediaConfig, ServicesConfig


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
                side_effect=RuntimeError("Anime audio request failed: 404 missing"),
            ),
            self.assertRaisesRegex(RuntimeError, "404 missing"),
        ):
            client.series(999)


if __name__ == "__main__":
    unittest.main()
