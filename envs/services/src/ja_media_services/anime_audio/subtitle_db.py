"""Subtitle read queries for the anime-audio index."""

from __future__ import annotations

import sqlite3
from typing import Any


def fetch_subtitles(
    connection: sqlite3.Connection,
    anilist_id: int,
    episode_key: str | None = None,
) -> list[dict[str, Any]]:
    """Return indexed subtitle rows for one series or episode."""

    if episode_key is None:
        rows = connection.execute(
            """
            SELECT * FROM subtitle WHERE anilist_id = ?
            ORDER BY CAST(episode_key AS INTEGER), episode_key, subtitle_id
            """,
            (anilist_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT * FROM subtitle WHERE anilist_id = ? AND episode_key = ?
            ORDER BY subtitle_id
            """,
            (anilist_id, episode_key),
        ).fetchall()
    return [_subtitle_mapping(row) for row in rows]


def fetch_subtitle(
    connection: sqlite3.Connection,
    anilist_id: int,
    episode_key: str,
    subtitle_id: str,
) -> dict[str, Any] | None:
    """Return one indexed subtitle row by stable stream ID."""

    row = connection.execute(
        """
        SELECT * FROM subtitle
        WHERE anilist_id = ? AND episode_key = ? AND subtitle_id = ?
        """,
        (anilist_id, episode_key, subtitle_id),
    ).fetchone()
    return _subtitle_mapping(row) if row else None


def _subtitle_mapping(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "anilist_id": int(row["anilist_id"]),
        "episode_key": str(row["episode_key"]),
        "subtitle_id": str(row["subtitle_id"]),
        "language": row["language"],
        "title": row["title"],
        "codec": str(row["codec"]),
        "default": bool(row["is_default"]),
        "filename": str(row["relative_path"]),
        "size_bytes": int(row["size_bytes"]),
        "source_stream_index": int(row["source_stream_index"]),
        "source_stream_ordinal": int(row["source_stream_ordinal"]),
        "sha256": row["sha256"],
        "created_at": str(row["created_at"]),
    }
