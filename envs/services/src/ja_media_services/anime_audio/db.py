"""SQLite schema and read queries for the anime-audio index."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2"


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
          manifest_path TEXT NOT NULL UNIQUE,
          manifest_mtime_ns INTEGER,
          manifest_size INTEGER
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
          detail TEXT NOT NULL,
          manifest_mtime_ns INTEGER,
          manifest_size INTEGER
        );
        """
    )
    _add_column(connection, "series", "manifest_mtime_ns", "INTEGER")
    _add_column(connection, "series", "manifest_size", "INTEGER")
    _add_column(connection, "reconciliation_error", "manifest_mtime_ns", "INTEGER")
    _add_column(connection, "reconciliation_error", "manifest_size", "INTEGER")
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
        "watcher_enabled": metadata.get("watcher_enabled") == "1",
        "watcher_running": metadata.get("watcher_running") == "1",
        "fallback_scan_running": metadata.get("fallback_scan_running") == "1",
        "last_incremental_scan": metadata.get("last_incremental_scan"),
        "incremental_scan_failures": int(
            metadata.get("incremental_scan_failures", "0")
        ),
        "refresh_failures": int(metadata.get("refresh_failures", "0")),
    }


def fetch_inventory(connection: sqlite3.Connection) -> dict[str, Any]:
    """Project the complete index as a path-free inventory snapshot.

    Series are ordered by ``anilist_id``; episode keys and artifact profiles
    within each series preserve the same ordering used by point lookups. The
    result reuses the same SQLite snapshot as ``fetch_series`` and
    ``fetch_artifacts`` so consumers see a consistent projection.
    """

    series_rows = connection.execute(
        "SELECT * FROM series ORDER BY anilist_id"
    ).fetchall()
    artifact_rows = connection.execute(
        """
        SELECT anilist_id, episode_key, profile FROM artifact
        ORDER BY anilist_id, CAST(episode_key AS INTEGER), episode_key, profile
        """
    ).fetchall()

    grouped: dict[int, dict[str, Any]] = {}
    for row in artifact_rows:
        aid = int(row["anilist_id"])
        bucket = grouped.setdefault(aid, {"episodes": {}, "profiles": {}, "artifacts": 0})
        bucket["artifacts"] += 1
        bucket["episodes"][str(row["episode_key"])] = None
        bucket["profiles"][str(row["profile"])] = None

    series_list: list[dict[str, Any]] = []
    total_episodes = 0
    total_artifacts = 0
    for row in series_rows:
        aid = int(row["anilist_id"])
        bucket = grouped.get(aid)
        episode_keys = tuple(bucket["episodes"]) if bucket else ()
        profiles = tuple(bucket["profiles"]) if bucket else ()
        episode_count = len(episode_keys)
        artifact_count = bucket["artifacts"] if bucket else 0
        total_episodes += episode_count
        total_artifacts += artifact_count
        series_list.append(
            {
                "anilist_id": aid,
                "title": str(row["title"]),
                "title_english": row["title_english"],
                "title_native": row["title_native"],
                "title_romaji": row["title_romaji"],
                "profile": str(row["profile"]),
                "episode_count": episode_count,
                "artifact_count": artifact_count,
                "episode_keys": episode_keys,
                "artifact_profiles": profiles,
            }
        )
    return {
        "series_count": len(series_list),
        "episode_count": total_episodes,
        "artifact_count": total_artifacts,
        "series": series_list,
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


def increment_metadata(connection: sqlite3.Connection, key: str) -> None:
    """Increment one integer operational counter."""

    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?", (key,)
    ).fetchone()
    value = int(row["value"]) + 1 if row else 1
    set_metadata(connection, key, str(value))


def _add_column(
    connection: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    columns = {
        str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
