from __future__ import annotations

import json
from pathlib import Path

from ja_media_services.anime_crosswalk.db import connect_readonly, fetch_lookup, fetch_metadata
from ja_media_services.anime_crosswalk.ingest import build_database, lookup_rows


def write_fixture(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "type": "MOVIE",
                    "anidb_id": 5459,
                    "mal_id": 3269,
                    "tvdb_id": 79099,
                    "imdb_id": "tt1164545",
                    "themoviedb_id": {"tv": 8864, "movie": 128},
                },
                {
                    "type": "TV",
                    "anidb_id": 1,
                    "mal_id": 1,
                    "tvdb_id": 111,
                    "themoviedb_id": {"tv": 222},
                    "season": {"tvdb": 1},
                },
            ]
        ),
        encoding="utf-8",
    )


def test_lookup_rows_include_kind_specific_tvdb_and_tmdb() -> None:
    rows = lookup_rows(
        {
            "type": "MOVIE",
            "anidb_id": 5459,
            "tvdb_id": 79099,
            "themoviedb_id": {"movie": 128},
        },
        1,
    )

    assert ("tvdb", "79099", None, 1) in rows
    assert ("tvdb", "79099", "movie", 1) in rows
    assert ("tmdb", "128", "movie", 1) in rows


def test_build_database_creates_queryable_lookup_db(tmp_path: Path) -> None:
    input_path = tmp_path / "anime-list-full.json"
    output_path = tmp_path / "anime_lists.sqlite"
    write_fixture(input_path)

    metadata = build_database(
        input_path=input_path,
        output_path=output_path,
        source_repo="Fribb/anime-lists",
        source_branch="master",
        source_commit="abc123",
    )

    assert metadata["anime_count"] == "2"
    connection = connect_readonly(output_path)
    try:
        stats = fetch_metadata(connection)
        assert stats["source_commit"] == "abc123"
        results = fetch_lookup(
            connection,
            source="tvdb",
            external_id="79099",
            media_kind="movie",
        )
        assert results[0]["anidb_id"] == 5459
    finally:
        connection.close()
