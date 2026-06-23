from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ja_media_services.anime_audio.app import create_app
from ja_media_services.anime_audio.db import fetch_inventory, initialize, set_metadata
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


def test_metrics_expose_last_reconciliation_timestamp(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "anime_audio_last_reconciliation_timestamp_seconds" in response.text
    # The startup reconcile stamps last_success, so the gauge must be non-zero.
    for line in response.text.splitlines():
        if line.startswith("anime_audio_last_reconciliation_timestamp_seconds"):
            assert float(line.split()[1]) > 0.0
            return
    raise AssertionError("last_reconciliation metric not found")


def _episode(episode_key: str, *, artifact_path: str) -> dict[str, object]:
    return {
        "episode_key": episode_key,
        "source": {
            "relative_path": f"Episode {episode_key}.mkv",
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


def _multi_episode_manifest(anilist_id: int, title: str) -> dict[str, object]:
    """A manifest with episodes 1 and 10 to verify numeric key ordering."""
    payload = manifest(anilist_id=anilist_id, title=title, artifact_path="E01.m4a")
    payload["episodes"] = [
        _episode("10", artifact_path="E10.m4a"),
        _episode("1", artifact_path="E01.m4a"),
    ]
    return payload


def _write_manifest(library: Path, directory: str, payload: dict[str, object]) -> None:
    series = library / directory
    series.mkdir(parents=True, exist_ok=True)
    for episode in payload["episodes"]:  # type: ignore[union-attr]
        artifact = str(episode["artifact"]["relative_path"])  # type: ignore[index]
        (series / artifact).write_bytes(b"audio-bytes")
    (series / ".ja-media.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_inventory_is_empty_when_nothing_indexed(tmp_path: Path) -> None:
    settings = AnimeAudioSettings(
        library_root=tmp_path / "library",
        db_path=tmp_path / "index.sqlite",
    )
    (tmp_path / "library").mkdir()
    client = TestClient(create_app(settings))

    response = client.get("/inventory")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "series_count": 0,
        "episode_count": 0,
        "artifact_count": 0,
        "series": [],
    }


def test_inventory_projects_all_series_with_deterministic_ordering(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    _write_manifest(library, "anilist-10", _multi_episode_manifest(10, "Beta"))
    _write_manifest(library, "anilist-2", _multi_episode_manifest(2, "Alpha"))

    settings = AnimeAudioSettings(
        library_root=library,
        db_path=tmp_path / "index.sqlite",
    )
    client = TestClient(create_app(settings))

    body = client.get("/inventory").json()

    assert body["series_count"] == 2
    assert body["episode_count"] == 4
    assert body["artifact_count"] == 4
    # Series ordered by anilist_id, not directory name.
    assert [series["anilist_id"] for series in body["series"]] == [2, 10]
    first = body["series"][0]
    assert first["title"] == "Alpha"
    # Episode keys sorted numerically ("1" before "10"), not lexically.
    assert list(first["episode_keys"]) == ["1", "10"]
    assert list(first["artifact_profiles"]) == ["portable-aac-v1"]
    assert first["episode_count"] == 2
    assert first["artifact_count"] == 2


def test_inventory_omits_filesystem_paths(tmp_path: Path) -> None:
    client = _client(tmp_path)

    body = client.get("/inventory").json()

    serialized = json.dumps(body)
    assert "manifest_path" not in serialized
    assert "relative_path" not in serialized
    assert "filename" not in serialized


def test_inventory_projects_multiple_profiles_per_series(tmp_path: Path) -> None:
    """The artifact table permits multiple profiles per episode even though
    the current manifest format only writes one. Verify the projection groups
    them correctly by inserting rows directly into the SQLite index."""
    connection = initialize(tmp_path / "index.sqlite")
    connection.execute(
        "INSERT INTO series(anilist_id, title, title_english, title_native, "
        "title_romaji, profile, manifest_path, manifest_mtime_ns, manifest_size) "
        "VALUES (5, 'Gamma', 'Gamma', NULL, 'Gamma', 'portable-aac-v1', "
        "'anilist-5/.ja-media.json', 1, 1)"
    )
    artifact_rows = [
        (5, "1", "portable-aac-v1", "E01.m4a", 10, 1000, "aac", 128000, 2, 48000, "abc", "2026-01-01T00:00:00+00:00"),
        (5, "1", "portable-opus-v1", "E01.opus", 8, 1000, "opus", 96000, 2, 48000, "def", "2026-01-01T00:00:00+00:00"),
        (5, "2", "portable-aac-v1", "E02.m4a", 10, 1000, "aac", 128000, 2, 48000, "ghi", "2026-01-01T00:00:00+00:00"),
    ]
    connection.executemany(
        "INSERT INTO artifact(anilist_id, episode_key, profile, relative_path, "
        "size_bytes, duration_ms, codec, bitrate_bps, channels, sample_rate_hz, "
        "sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        artifact_rows,
    )
    set_metadata(connection, "ready", "1")
    connection.commit()

    inventory = fetch_inventory(connection)

    assert inventory["series_count"] == 1
    assert inventory["episode_count"] == 2
    assert inventory["artifact_count"] == 3
    series = inventory["series"][0]
    assert list(series["episode_keys"]) == ["1", "2"]
    assert list(series["artifact_profiles"]) == [
        "portable-aac-v1",
        "portable-opus-v1",
    ]
    assert series["artifact_count"] == 3
    connection.close()
