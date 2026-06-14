from __future__ import annotations

import sqlite3
from pathlib import Path


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
          subtitle_id INTEGER PRIMARY KEY,
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

        CREATE TABLE subtitle_lookup (
          source TEXT NOT NULL,
          external_id TEXT NOT NULL,
          media_kind TEXT,
          anilist_id INTEGER NOT NULL,
          subtitle_id INTEGER NOT NULL REFERENCES subtitle_file(subtitle_id),
          PRIMARY KEY (source, external_id, media_kind, subtitle_id)
        );

        CREATE INDEX subtitle_lookup_source_id_idx
        ON subtitle_lookup(source, external_id);

        CREATE INDEX subtitle_lookup_source_id_kind_idx
        ON subtitle_lookup(source, external_id, media_kind);

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

    required_tables = {"subtitle_file", "subtitle_lookup", "metadata"}
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
    return metadata
