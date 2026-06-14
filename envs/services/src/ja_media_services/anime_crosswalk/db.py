from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class LookupRow:
    source: str
    external_id: str
    media_kind: str | None
    row_id: int


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a generated SQLite database in read-only URI mode."""

    uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create the generated DB schema used by the service."""

    connection.executescript(
        """
        CREATE TABLE anime (
          row_id INTEGER PRIMARY KEY,
          anidb_id INTEGER,
          payload_json TEXT NOT NULL
        );

        CREATE TABLE lookup (
          source TEXT NOT NULL,
          external_id TEXT NOT NULL,
          media_kind TEXT,
          row_id INTEGER NOT NULL REFERENCES anime(row_id),
          PRIMARY KEY (source, external_id, media_kind, row_id)
        );

        CREATE INDEX lookup_source_id_idx
        ON lookup(source, external_id);

        CREATE INDEX lookup_source_id_kind_idx
        ON lookup(source, external_id, media_kind);

        CREATE TABLE metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )


def fetch_lookup(
    connection: sqlite3.Connection,
    *,
    source: str,
    external_id: str,
    media_kind: str | None,
) -> list[dict[str, Any]]:
    """Return all payloads matching a source ID and optional media kind."""

    rows = connection.execute(
        """
        SELECT anime.payload_json
        FROM lookup
        JOIN anime USING (row_id)
        WHERE lookup.source = ?
          AND lookup.external_id = ?
          AND (? IS NULL OR lookup.media_kind = ?)
        ORDER BY anime.row_id
        """,
        (source, external_id, media_kind, media_kind),
    ).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


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
    finally:
        connection.close()

    anime_count = int(metadata.get("anime_count", "0"))
    lookup_count = int(metadata.get("lookup_count", "0"))
    if anime_count <= 0:
        raise ValueError("Generated DB has no anime rows")
    if lookup_count <= anime_count:
        raise ValueError("Generated DB lookup count should exceed anime row count")
    for key in (
        "tvdb_lookup_count",
        "mal_lookup_count",
        "anidb_lookup_count",
        "tmdb_lookup_count",
    ):
        if int(metadata.get(key, "0")) <= 0:
            raise ValueError(f"Generated DB has no {key}")
    return metadata
