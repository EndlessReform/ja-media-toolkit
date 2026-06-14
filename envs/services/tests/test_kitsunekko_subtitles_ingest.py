from __future__ import annotations

import sqlite3
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
        connection.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            [
                ("source_repo", "Fribb/anime-lists"),
                ("source_branch", "master"),
                ("source_commit", "abc123"),
                ("schema_version", "1"),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_build_database_records_crosswalk_snapshot(tmp_path: Path) -> None:
    crosswalk_db = tmp_path / "anime_lists.sqlite"
    output_path = tmp_path / "kitsunekko_subtitles.sqlite"
    write_crosswalk_db(crosswalk_db)

    metadata = build_database(
        output_path=output_path,
        crosswalk_db_path=crosswalk_db,
        mirror_repo="Ajatt-Tools/kitsunekko-mirror",
        mirror_branch="main",
        mirror_commit="not-synced",
    )

    assert metadata["ingest_phase"] == "crosswalk-only"
    assert metadata["crosswalk_source_commit"] == "abc123"
    assert metadata["subtitle_row_count"] == "0"

    connection = connect_readonly(output_path)
    try:
        stats = fetch_metadata(connection)
        assert stats["mirror_repo"] == "Ajatt-Tools/kitsunekko-mirror"
        assert stats["lookup_row_count"] == "0"
    finally:
        connection.close()

    validate_generated_db(output_path)
