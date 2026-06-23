from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ja_media_services.anime_audio.app import create_app
from ja_media_services.anime_audio.settings import AnimeAudioSettings
from anime_audio_support import manifest, write_series


def _client(tmp_path: Path, *, manifest: dict[str, object] | None = None) -> TestClient:
    library = tmp_path / "library"
    write_series(
        library,
        payload=manifest,
        write_artifact=manifest is None
        or manifest["episodes"][0]["artifact"]["relative_path"] == "S01E001.m4a",  # type: ignore[index]
    )
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
    client = _client(tmp_path, manifest=manifest(artifact_path="missing.m4a"))

    health = client.get("/healthz")

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["error_count"] == 1
    assert client.get("/series/1").status_code == 404


def test_path_escape_is_rejected_during_reconciliation(tmp_path: Path) -> None:
    client = _client(tmp_path, manifest=manifest(artifact_path="../outside.m4a"))

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
    assert "anime_audio_watcher_running 0.0" in response.text
    assert "anime_audio_last_incremental_scan_timestamp_seconds 0.0" in response.text
    assert "anime_audio_manifest_refresh_failures_total 0.0" in response.text
