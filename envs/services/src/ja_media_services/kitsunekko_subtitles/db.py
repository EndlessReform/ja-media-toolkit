from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1"


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a generated Kitsunekko subtitle database in read-only URI mode."""

    uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create the generated DB schema used by the subtitle service."""

    connection.executescript(
        """
        CREATE TABLE subtitle_file (
          subtitle_id TEXT PRIMARY KEY,
          anilist_id INTEGER NOT NULL,
          repo_path TEXT NOT NULL UNIQUE,
          filename TEXT NOT NULL,
          extension TEXT NOT NULL,
          episode_local INTEGER,
          episode_absolute INTEGER,
          episode_raw TEXT,
          episode_confidence TEXT NOT NULL,
          group_hint TEXT,
          language_hint TEXT,
          release_tags_json TEXT NOT NULL,
          last_modified TEXT
        );

        CREATE INDEX subtitle_file_anilist_idx
        ON subtitle_file(anilist_id);

        CREATE INDEX subtitle_file_episode_idx
        ON subtitle_file(anilist_id, episode_local);

        CREATE TABLE metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )


def fetch_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    """Return all generated DB metadata as strings."""

    return {
        str(row["key"]): str(row["value"])
        for row in connection.execute("SELECT key, value FROM metadata ORDER BY key")
    }


def row_to_subtitle_file(row: sqlite3.Row) -> dict[str, Any]:
    """Convert one DB subtitle row into the API response shape."""

    return {
        "subtitle_id": str(row["subtitle_id"]),
        "anilist_id": int(row["anilist_id"]),
        "repo_path": str(row["repo_path"]),
        "filename": str(row["filename"]),
        "extension": str(row["extension"]),
        "episode_local": row["episode_local"],
        "episode_absolute": row["episode_absolute"],
        "episode_raw": row["episode_raw"],
        "episode_confidence": str(row["episode_confidence"]),
        "group_hint": row["group_hint"],
        "language_hint": row["language_hint"],
        "release_tags": json_loads_list(str(row["release_tags_json"])),
        "last_modified": row["last_modified"],
    }


def json_loads_list(value: str) -> list[str]:
    """Parse a stored JSON list while keeping API rendering defensive."""

    import json

    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def fetch_files_by_anilist(connection: sqlite3.Connection, anilist_id: int) -> list[dict[str, Any]]:
    """Return subtitle rows directly indexed under one AniList ID."""

    rows = connection.execute(
        """
        SELECT *
        FROM subtitle_file
        WHERE anilist_id = ?
        ORDER BY episode_local IS NULL, episode_local, repo_path
        """,
        (anilist_id,),
    ).fetchall()
    return [row_to_subtitle_file(row) for row in rows]


def fetch_files_by_anilist_with_prefix(
    connection: sqlite3.Connection,
    anilist_id: int,
    prefix: str | None,
) -> list[dict[str, Any]]:
    """Return subtitle rows for one AniList ID, optionally scoped by repo path prefix."""

    if not prefix:
        return fetch_files_by_anilist(connection, anilist_id)
    rows = connection.execute(
        """
        SELECT *
        FROM subtitle_file
        WHERE anilist_id = ?
          AND repo_path LIKE ? || '%'
        ORDER BY episode_local IS NULL, episode_local, repo_path
        """,
        (anilist_id, prefix),
    ).fetchall()
    return [row_to_subtitle_file(row) for row in rows]


def fetch_files_by_anilist_ids(
    connection: sqlite3.Connection,
    anilist_ids: list[int],
) -> list[dict[str, Any]]:
    """Return subtitle rows for several AniList IDs."""

    if not anilist_ids:
        return []
    placeholders = ",".join("?" for _ in anilist_ids)
    rows = connection.execute(
        f"""
        SELECT *
        FROM subtitle_file
        WHERE anilist_id IN ({placeholders})
        ORDER BY anilist_id,
                 episode_local IS NULL,
                 episode_local,
                 repo_path
        """,
        tuple(anilist_ids),
    ).fetchall()
    return [row_to_subtitle_file(row) for row in rows]


def fetch_files_by_anilist_ids_with_prefix(
    connection: sqlite3.Connection,
    anilist_ids: list[int],
    prefix: str | None,
) -> list[dict[str, Any]]:
    """Return subtitle rows for several AniList IDs, optionally scoped by repo path prefix."""

    if not prefix:
        return fetch_files_by_anilist_ids(connection, anilist_ids)
    if not anilist_ids:
        return []
    placeholders = ",".join("?" for _ in anilist_ids)
    rows = connection.execute(
        f"""
        SELECT *
        FROM subtitle_file
        WHERE anilist_id IN ({placeholders})
          AND repo_path LIKE ? || '%'
        ORDER BY anilist_id,
                 episode_local IS NULL,
                 episode_local,
                 repo_path
        """,
        (*anilist_ids, prefix),
    ).fetchall()
    return [row_to_subtitle_file(row) for row in rows]


def fetch_file_by_subtitle_id(
    connection: sqlite3.Connection,
    subtitle_id: str,
) -> dict[str, Any] | None:
    """Return one subtitle row by stable UUID."""

    row = connection.execute(
        "SELECT * FROM subtitle_file WHERE subtitle_id = ?",
        (subtitle_id,),
    ).fetchone()
    return row_to_subtitle_file(row) if row is not None else None


def fetch_files_by_repo_path(
    connection: sqlite3.Connection,
    repo_path: str,
) -> list[dict[str, Any]]:
    """Return subtitle rows matching an exact mirror-relative repository path."""

    rows = connection.execute(
        "SELECT * FROM subtitle_file WHERE repo_path = ? ORDER BY repo_path",
        (repo_path,),
    ).fetchall()
    return [row_to_subtitle_file(row) for row in rows]


def fetch_files_by_filename(
    connection: sqlite3.Connection,
    filename: str,
) -> list[dict[str, Any]]:
    """Return subtitle rows matching an exact filename."""

    rows = connection.execute(
        "SELECT * FROM subtitle_file WHERE filename = ? ORDER BY repo_path",
        (filename,),
    ).fetchall()
    return [row_to_subtitle_file(row) for row in rows]


def validate_generated_db(db_path: Path) -> dict[str, str]:
    """Validate minimum invariants before a generated DB is promoted."""

    if not db_path.exists():
        raise FileNotFoundError(f"Generated DB does not exist: {db_path}")
    connection = connect_readonly(db_path)
    try:
        metadata = fetch_metadata(connection)
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        }
    finally:
        connection.close()

    required_tables = {"subtitle_file", "metadata"}
    missing_tables = required_tables - tables
    if missing_tables:
        raise ValueError(f"Generated DB is missing tables: {sorted(missing_tables)}")
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema version: {metadata.get('schema_version', '<missing>')}"
        )
    for key in ("crosswalk_source_commit", "built_at", "subtitle_row_count", "lookup_row_count"):
        if key not in metadata:
            raise ValueError(f"Generated DB metadata is missing {key}")
    if int(metadata.get("subtitle_row_count", "0")) < 0:
        raise ValueError("Generated DB subtitle row count is invalid")
    if int(metadata.get("lookup_row_count", "0")) < 0:
        raise ValueError("Generated DB lookup row count is invalid")
    return metadata
