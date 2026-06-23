from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ja_media_services.anime_audio.app import create_app
from ja_media_services.anime_audio.settings import AnimeAudioSettings


def _manifest(*, artifact_path: str = "S01E001.m4a") -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "anime-audio-series",
        "series": {
            "anilist_id": 1,
            "title_english": "Example",
            "title_native": "例",
            "title_romaji": "Example",
            "title_preferred": "Example",
            "description_html": None,
            "description_text": None,
            "format": "TV",
            "status": "FINISHED",
            "season": "SPRING",
            "season_year": 2026,
            "episode_count": 1,
            "typical_duration_minutes": 24,
            "start_date": "2026-04-01",
            "end_date": None,
            "genres": ["Drama"],
            "source": "ORIGINAL",
            "country_of_origin": "JP",
            "banner_url": None,
            "mal_id": 2,
            "site_url": "https://anilist.co/anime/1",
            "upstream_updated_at": 1,
            "metadata_snapshot": {"title_english": "Example"},
            "cover": None,
        },
        "profile": {
            "name": "portable-aac-v1",
            "container": "m4a",
            "codec": "aac",
            "bitrate_bps": 128000,
            "max_channels": 2,
            "sample_rate_hz": 48000,
        },
        "episodes": [
            {
                "episode_key": "1",
                "source": {
                    "relative_path": "Episode 01.mkv",
                    "size_bytes": 100,
                    "mtime_ns": 1,
                    "global_stream_index": 1,
                    "audio_stream_ordinal": 0,
                    "audio_codec": "flac",
                    "audio_language": "jpn",
                },
                "artifact": {
                    "relative_path": artifact_path,
                    "size_bytes": 10,
                    "duration_ms": 1000,
                    "codec": "aac",
                    "bitrate_bps": 128000,
                    "channels": 2,
                    "sample_rate_hz": 48000,
                    "sha256": "abc",
                },
                "created_at": "2026-06-01T00:00:00+00:00",
            }
        ],
    }


def _client(tmp_path: Path, *, manifest: dict[str, object] | None = None) -> TestClient:
    library = tmp_path / "library"
    series = library / "anilist-1"
    series.mkdir(parents=True)
    (series / ".ja-media.json").write_text(
        json.dumps(manifest or _manifest()), encoding="utf-8"
    )
    (series / "S01E001.m4a").write_bytes(b"audio-bytes")
    settings = AnimeAudioSettings(
        library_root=library,
        db_path=tmp_path / "index.sqlite",
    )
    return TestClient(create_app(settings))


def test_indexes_lookup_and_serves_content(tmp_path: Path) -> None:
    client = _client(tmp_path)

    series = client.get("/series/1")
    episodes = client.get("/series/1/episodes")
    artifact = client.get(
        "/series/1/episodes/1/artifacts/portable-aac-v1"
    )
    content = client.get(
        "/series/1/episodes/1/artifacts/portable-aac-v1/content"
    )

    assert series.status_code == 200
    assert series.json()["episode_count"] == 1
    assert episodes.json()[0]["episode_key"] == "1"
    assert artifact.json()["filename"] == "S01E001.m4a"
    assert "manifest_path" not in artifact.json()
    assert content.content == b"audio-bytes"

    partial = client.get(
        "/series/1/episodes/1/artifacts/portable-aac-v1/content",
        headers={"Range": "bytes=0-4"},
    )
    assert partial.status_code == 206
    assert partial.content == b"audio"


def test_reconcile_removes_stale_rows_after_complete_scan(tmp_path: Path) -> None:
    client = _client(tmp_path)
    manifest = tmp_path / "library" / "anilist-1" / ".ja-media.json"
    manifest.unlink()

    response = client.post("/reconcile")

    assert response.status_code == 200
    assert response.json()["series_count"] == 0
    assert client.get("/series/1").status_code == 404


def test_missing_artifact_is_degraded_and_not_indexed(tmp_path: Path) -> None:
    client = _client(tmp_path, manifest=_manifest(artifact_path="missing.m4a"))

    health = client.get("/healthz")

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["error_count"] == 1
    assert client.get("/series/1").status_code == 404


def test_path_escape_is_rejected_during_reconciliation(tmp_path: Path) -> None:
    client = _client(tmp_path, manifest=_manifest(artifact_path="../outside.m4a"))

    health = client.get("/healthz")

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["error_count"] == 1


def test_unavailable_library_is_unhealthy(tmp_path: Path) -> None:
    settings = AnimeAudioSettings(
        library_root=tmp_path / "missing",
        db_path=tmp_path / "index.sqlite",
    )
    client = TestClient(create_app(settings))

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "unavailable"


def test_metrics_expose_index_state(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "anime_audio_index_ready 1.0" in response.text
    assert "anime_audio_artifacts_total 1.0" in response.text
