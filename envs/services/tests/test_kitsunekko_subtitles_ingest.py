from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from ja_media_services.kitsunekko_subtitles.db import (
    connect_readonly,
    fetch_metadata,
    validate_generated_db,
)
from ja_media_services.kitsunekko_subtitles.ingest import build_database


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
    (title_dir / "[Group] GANTZ.S01E16.ja[cc].srt").write_text("1\n", encoding="utf-8")
    (title_dir / "[Other] GANTZ.S01E17.ja.srt").write_text("1\n", encoding="utf-8")


def test_build_database_indexes_subtitle_files_only(tmp_path: Path) -> None:
    crosswalk_db = tmp_path / "anime_lists.sqlite"
    mirror_dir = tmp_path / "kitsunekko-mirror"
    output_path = tmp_path / "kitsunekko_subtitles.sqlite"
    write_crosswalk_db(crosswalk_db)
    write_mirror(mirror_dir)

    metadata = build_database(
        output_path=output_path,
        crosswalk_db_path=crosswalk_db,
        mirror_dir=mirror_dir,
        mirror_repo="Ajatt-Tools/kitsunekko-mirror",
        mirror_branch="main",
        mirror_commit="def456",
    )

    assert metadata["ingest_phase"] == "indexed"
    assert metadata["crosswalk_source_commit"] == "abc123"
    assert metadata["mirror_commit"] == "def456"
    assert metadata["subtitle_row_count"] == "2"

    connection = connect_readonly(output_path)
    try:
        stats = fetch_metadata(connection)
        assert stats["mirror_repo"] == "Ajatt-Tools/kitsunekko-mirror"
        assert stats["lookup_row_count"] == "0"
        file_row = connection.execute("SELECT * FROM subtitle_file").fetchone()
        uuid.UUID(file_row["subtitle_id"])
        assert file_row["anilist_id"] == 395
        assert file_row["episode_local"] == 16
        assert file_row["language_hint"] == "ja"
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "subtitle_lookup" not in tables
    finally:
        connection.close()

    validate_generated_db(output_path)
