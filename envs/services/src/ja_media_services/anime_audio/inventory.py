"""Inventory read-model projection for the anime-audio index."""

from __future__ import annotations

import sqlite3
from typing import Any


def fetch_inventory(connection: sqlite3.Connection) -> dict[str, Any]:
    """Project the complete index as a path-free inventory snapshot."""

    series_rows = connection.execute(
        "SELECT * FROM series ORDER BY anilist_id"
    ).fetchall()
    artifact_rows = connection.execute(
        """
        SELECT anilist_id, episode_key, profile FROM artifact
        ORDER BY anilist_id, CAST(episode_key AS INTEGER), episode_key, profile
        """
    ).fetchall()
    subtitle_rows = connection.execute(
        """
        SELECT anilist_id, episode_key, language FROM subtitle
        ORDER BY anilist_id, CAST(episode_key AS INTEGER), episode_key, subtitle_id
        """
    ).fetchall()

    grouped: dict[int, dict[str, Any]] = {}
    for row in artifact_rows:
        aid = int(row["anilist_id"])
        bucket = grouped.setdefault(aid, _empty_bucket())
        bucket["artifacts"] += 1
        bucket["episodes"][str(row["episode_key"])] = None
        bucket["profiles"][str(row["profile"])] = None
    for row in subtitle_rows:
        aid = int(row["anilist_id"])
        bucket = grouped.setdefault(aid, _empty_bucket())
        bucket["subtitles"] += 1
        bucket["episodes"][str(row["episode_key"])] = None
        language = row["language"]
        if language:
            bucket["languages"][str(language)] = None

    series_list: list[dict[str, Any]] = []
    total_episodes = total_artifacts = total_subtitles = 0
    for row in series_rows:
        aid = int(row["anilist_id"])
        bucket = grouped.get(aid)
        episode_keys = tuple(bucket["episodes"]) if bucket else ()
        profiles = tuple(bucket["profiles"]) if bucket else ()
        episode_count = len(episode_keys)
        artifact_count = bucket["artifacts"] if bucket else 0
        subtitle_count = bucket.get("subtitles", 0) if bucket else 0
        total_episodes += episode_count
        total_artifacts += artifact_count
        total_subtitles += subtitle_count
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
                "subtitle_count": subtitle_count,
                "episode_keys": episode_keys,
                "artifact_profiles": profiles,
                "subtitle_languages": (
                    tuple(bucket.get("languages", {})) if bucket else ()
                ),
            }
        )
    return {
        "series_count": len(series_list),
        "episode_count": total_episodes,
        "artifact_count": total_artifacts,
        "subtitle_count": total_subtitles,
        "series": series_list,
    }


def _empty_bucket() -> dict[str, Any]:
    return {
        "episodes": {},
        "profiles": {},
        "artifacts": 0,
        "subtitles": 0,
        "languages": {},
    }
