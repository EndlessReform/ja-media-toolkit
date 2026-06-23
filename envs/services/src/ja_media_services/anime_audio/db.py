"""SQLite schema and read queries for the anime-audio index."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the service index and configure dictionary-like rows."""

    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(db_path: Path) -> sqlite3.Connection:
    """Create the parent directory and schema when needed."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_path)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS series (
          anilist_id INTEGER PRIMARY KEY,
          title TEXT NOT NULL,
          title_english TEXT,
          title_native TEXT,
          title_romaji TEXT,
          profile TEXT NOT NULL,
          manifest_path TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS artifact (
          anilist_id INTEGER NOT NULL REFERENCES series(anilist_id) ON DELETE CASCADE,
          episode_key TEXT NOT NULL,
          profile TEXT NOT NULL,
          relative_path TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,
          duration_ms INTEGER NOT NULL,
          codec TEXT NOT NULL,
          bitrate_bps INTEGER,
          channels INTEGER NOT NULL,
          sample_rate_hz INTEGER NOT NULL,
          sha256 TEXT,
          created_at TEXT NOT NULL,
          PRIMARY KEY (anilist_id, episode_key, profile)
        );
        CREATE TABLE IF NOT EXISTS reconciliation_error (
          manifest_path TEXT NOT NULL,
          error_code TEXT NOT NULL,
          detail TEXT NOT NULL
        );
        """
    )
    set_metadata(connection, "schema_version", SCHEMA_VERSION)
    connection.commit()
    return connection


def stats(connection: sqlite3.Connection) -> dict[str, Any]:
    """Return low-cardinality index and reconciliation state."""

    metadata = {
        str(row["key"]): str(row["value"])
        for row in connection.execute("SELECT key, value FROM metadata")
    }
    return {
        "ready": metadata.get("ready") == "1",
        "series_count": count_rows(connection, "series"),
        "artifact_count": count_rows(connection, "artifact"),
        "error_count": count_rows(connection, "reconciliation_error"),
        "last_attempt": metadata.get("last_attempt"),
        "last_success": metadata.get("last_success"),
        "last_failure_code": metadata.get("last_failure_code") or None,
    }


def fetch_series(connection: sqlite3.Connection, anilist_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM series WHERE anilist_id = ?", (anilist_id,)
    ).fetchone()
    if row is None:
        return None
    counts = connection.execute(
        """
        SELECT COUNT(DISTINCT episode_key) AS episodes, COUNT(*) AS artifacts
        FROM artifact WHERE anilist_id = ?
        """,
        (anilist_id,),
    ).fetchone()
    return {
        "anilist_id": int(row["anilist_id"]),
        "title": str(row["title"]),
        "title_english": row["title_english"],
        "title_native": row["title_native"],
        "title_romaji": row["title_romaji"],
        "profile": str(row["profile"]),
        "episode_count": int(counts["episodes"]),
        "artifact_count": int(counts["artifacts"]),
    }


def fetch_artifacts(connection: sqlite3.Connection, anilist_id: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM artifact WHERE anilist_id = ?
        ORDER BY CAST(episode_key AS INTEGER), episode_key, profile
        """,
        (anilist_id,),
    ).fetchall()
    return [_artifact_mapping(row) for row in rows]


def fetch_artifact(
    connection: sqlite3.Connection,
    anilist_id: int,
    episode_key: str,
    profile: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT * FROM artifact
        WHERE anilist_id = ? AND episode_key = ? AND profile = ?
        """,
        (anilist_id, episode_key, profile),
    ).fetchone()
    return _artifact_mapping(row) if row else None


def resolve_content_path(
    connection: sqlite3.Connection,
    library_root: Path,
    artifact: dict[str, Any],
) -> Path:
    """Resolve an indexed artifact while enforcing its series boundary."""

    row = connection.execute(
        "SELECT manifest_path FROM series WHERE anilist_id = ?",
        (artifact["anilist_id"],),
    ).fetchone()
    if row is None:
        raise FileNotFoundError("indexed series disappeared")
    return artifact_path(
        library_root.resolve() / Path(str(row["manifest_path"])).parent,
        str(artifact["filename"]),
    )


def artifact_path(series_root: Path, relative_path: str) -> Path:
    """Resolve a manifest artifact path without permitting escape."""

    if Path(relative_path).is_absolute():
        raise ValueError("artifact path must be relative")
    root = series_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("artifact path escapes its series directory") from error
    return candidate


def set_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", (key, value)
    )


def count_rows(connection: sqlite3.Connection, table: str) -> int:
    """Count a table selected only by trusted service code."""

    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _artifact_mapping(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "anilist_id": int(row["anilist_id"]),
        "episode_key": str(row["episode_key"]),
        "profile": str(row["profile"]),
        "filename": str(row["relative_path"]),
        "size_bytes": int(row["size_bytes"]),
        "duration_ms": int(row["duration_ms"]),
        "codec": str(row["codec"]),
        "bitrate_bps": row["bitrate_bps"],
        "channels": int(row["channels"]),
        "sample_rate_hz": int(row["sample_rate_hz"]),
        "sha256": row["sha256"],
        "created_at": str(row["created_at"]),
    }
