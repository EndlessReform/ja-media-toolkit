from __future__ import annotations

import gzip
import io
import json
import sqlite3
import tarfile
from pathlib import Path

from fastapi.testclient import TestClient

from ja_media_services.kitsunekko_subtitles.app import (
    content_disposition,
    create_app,
    get_connection,
    get_crosswalk_connection,
)
from ja_media_services.kitsunekko_subtitles.ingest import build_database
from ja_media_services.kitsunekko_subtitles.settings import KitsunekkoSubtitlesSettings

def write_crosswalk_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE anime (row_id INTEGER PRIMARY KEY, anidb_id INTEGER, payload_json TEXT NOT NULL)"
        )
        connection.execute(
            """
            CREATE TABLE lookup (
              source TEXT NOT NULL,
              external_id TEXT NOT NULL,
              media_kind TEXT,
              row_id INTEGER NOT NULL REFERENCES anime(row_id),
              PRIMARY KEY (source, external_id, media_kind, row_id)
            )
            """
        )
        connection.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            [
                ("source_repo", "Fribb/anime-lists"),
                ("source_branch", "master"),
                ("source_commit", "abc123"),
                ("schema_version", "1"),
            ],
        )
        connection.execute(
            "INSERT INTO anime (row_id, anidb_id, payload_json) VALUES (?, ?, ?)",
            (1, 1, json.dumps({"anilist_id": 395, "tvdb_id": 12345})),
        )
        connection.executemany(
            "INSERT INTO lookup (source, external_id, media_kind, row_id) VALUES (?, ?, ?, ?)",
            [
                ("anilist", "395", None, 1),
                ("tvdb", "12345", None, 1),
                ("tvdb", "12345", "tv", 1),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def write_mirror(path: Path) -> None:
    title_dir = path / "subtitles" / "anime_tv" / "GANTZ 2"
    title_dir.mkdir(parents=True, exist_ok=True)
    (title_dir / ".kitsuinfo.json").write_text(
        json.dumps(
            {
                "entry_id": 4017,
                "name": "GANTZ 2",
                "entry_type": "anime_tv",
                "last_modified": "2025-01-25T18:28:44Z",
                "anilist_id": 395,
            }
        ),
        encoding="utf-8",
    )
    (title_dir / "[Group] GANTZ.S01E16.ja[cc].srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n",
        encoding="utf-8",
    )
    (title_dir / "[Other] GANTZ.S01E17.ja.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nさようなら\n",
        encoding="utf-8",
    )


def make_client(tmp_path: Path) -> TestClient:
    crosswalk_db = tmp_path / "anime_lists.sqlite"
    mirror_dir = tmp_path / "kitsunekko-mirror"
    db_path = tmp_path / "kitsunekko_subtitles.sqlite"
    write_crosswalk_db(crosswalk_db)
    write_mirror(mirror_dir)
    build_database(
        output_path=db_path,
        crosswalk_db_path=crosswalk_db,
        mirror_dir=mirror_dir,
        mirror_repo="Ajatt-Tools/kitsunekko-mirror",
        mirror_branch="main",
        mirror_commit="def456",
    )
    get_connection.cache_clear()
    get_crosswalk_connection.cache_clear()
    settings = KitsunekkoSubtitlesSettings(
        db_path=db_path,
        crosswalk_db_path=crosswalk_db,
        mirror_dir=mirror_dir,
        repo_root=Path(__file__).resolve().parents[3],
    )
    return TestClient(create_app(settings))


def test_anilist_file_endpoint_returns_indexed_files(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/series/anilist/395/files")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["files"][0]["anilist_id"] == 395
    assert payload["files"][0]["episode_local"] == 16
    assert payload["files"][0]["subtitle_id"]


def test_tvdb_file_endpoint_resolves_anilist_at_runtime(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/series/tvdb/tv/12345/files")

    assert response.status_code == 200
    payload = response.json()
    assert payload["anilist_ids"] == [395]
    assert payload["count"] == 2
    assert payload["files"][0]["repo_path"].endswith(".srt")


def test_anilist_episode_file_endpoint_filters_with_runtime_filename_parse(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/series/anilist/395/episodes/16/files")

    assert response.status_code == 200
    payload = response.json()
    assert payload["episode_number"] == 16
    assert payload["count"] == 1
    assert payload["files"][0]["filename"] == "[Group] GANTZ.S01E16.ja[cc].srt"


def test_tvdb_episode_file_endpoint_resolves_then_filters(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/series/tvdb/tv/12345/episodes/17/files")

    assert response.status_code == 200
    payload = response.json()
    assert payload["anilist_ids"] == [395]
    assert payload["episode_number"] == 17
    assert payload["count"] == 1
    assert payload["files"][0]["filename"] == "[Other] GANTZ.S01E17.ja.srt"


def test_file_content_endpoint_fetches_by_uuid_and_supports_gzip(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    listed = client.get("/series/anilist/395/files").json()["files"][0]

    plain_response = client.get(f"/files/{listed['subtitle_id']}/content?compression=none")

    assert plain_response.status_code == 200
    assert plain_response.text.startswith("1\n")
    assert "こんにちは" in plain_response.text
    assert ".srt" in plain_response.headers["content-disposition"]

    gzip_response = client.get(f"/files/{listed['subtitle_id']}/content?compression=gzip")

    assert gzip_response.status_code == 200
    assert gzip_response.headers["content-encoding"] == "gzip"
    body = gzip_response.content
    if body.startswith(b"\x1f\x8b"):
        body = gzip.decompress(body)
    assert "こんにちは" in body.decode("utf-8")


def test_file_metadata_and_content_can_fetch_by_exact_filename(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    filename = "[Other] GANTZ.S01E17.ja.srt"

    metadata_response = client.get(f"/files/{filename}")
    content_response = client.get(f"/files/{filename}/content?compression=none")

    assert metadata_response.status_code == 200
    assert metadata_response.json()["episode_local"] == 17
    assert content_response.status_code == 200
    assert "さようなら" in content_response.text


def test_multiple_file_content_endpoint_accepts_uuid_and_repo_path_refs(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    files = client.get("/series/anilist/395/files").json()["files"]

    response = client.get(
        "/files/content",
        params=[
            ("ref", files[0]["subtitle_id"]),
            ("ref", files[1]["repo_path"]),
            ("compression", "gzip"),
        ],
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/gzip")
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        names = archive.getnames()

    assert files[0]["repo_path"] in names
    assert files[1]["repo_path"] in names


def test_series_content_endpoint_supports_prefix_filter_and_gzip(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get(
        "/series/anilist/395/content",
        params={
            "prefix": "subtitles/anime_tv/GANTZ 2/[Group]",
            "compression": "gzip",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/gzip")
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        names = archive.getnames()

    assert names == ["subtitles/anime_tv/GANTZ 2/[Group] GANTZ.S01E16.ja[cc].srt"]


def test_episode_content_endpoint_supports_prefix_filter_and_gzip(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get(
        "/series/tvdb/12345/episodes/16/content",
        params={
            "prefix": "subtitles/anime_tv/GANTZ 2/[Group]",
            "compression": "gzip",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/gzip")
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        names = archive.getnames()

    assert names == ["subtitles/anime_tv/GANTZ 2/[Group] GANTZ.S01E16.ja[cc].srt"]


def test_llms_txt_documents_episode_endpoints(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/llms.txt")

    assert response.status_code == 200
    assert "/series/anilist/395/episodes/16/files" in response.text
    assert "/series/tvdb/79099/episodes/16/files" in response.text
    assert "parse-torrent-title" in response.text


def test_content_disposition_ascii_only() -> None:
    value = content_disposition("episode1.srt")
    assert 'attachment; filename="episode1.srt"' in value
    assert "filename*=UTF-8''episode1.srt" in value


def test_content_disposition_unicode_filename() -> None:
    value = content_disposition("日本語字幕.srt")
    # ASCII fallback strips non-ASCII characters
    assert 'attachment; filename=".srt"' in value
    # RFC 5987 UTF-8 encoded original preserves the full filename
    import urllib.parse

    expected_encoded = urllib.parse.quote("日本語字幕.srt", encoding="utf-8")
    assert f"filename*=UTF-8''{expected_encoded}" in value


def test_content_disposition_sanitizes_path_separators() -> None:
    value = content_disposition("dir/subtitle\\file.srt")
    # Backslashes and slashes are replaced with underscores
    assert "dir_subtitle_file.srt" in value
