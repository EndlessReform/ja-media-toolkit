"""Public subtitle response helpers for the anime-audio API."""

from __future__ import annotations

import base64
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ja_media_services.anime_audio.db import resolve_content_path


def public_subtitle(subtitle: dict[str, Any]) -> dict[str, Any]:
    """Return path-free subtitle metadata with a stable content URL."""

    anilist_id = subtitle["anilist_id"]
    episode_key = subtitle["episode_key"]
    subtitle_id = subtitle["subtitle_id"]
    encoded_episode = urllib.parse.quote(str(episode_key), safe="")
    encoded_subtitle = urllib.parse.quote(str(subtitle_id), safe="")
    return {
        **subtitle,
        "content_url": (
            f"/series/{anilist_id}/episodes/{encoded_episode}/subtitles/"
            f"{encoded_subtitle}/content"
        ),
    }


def select_subtitles(
    subtitles: list[dict[str, Any]],
    *,
    ids: list[str] | None,
    getall: bool,
) -> list[dict[str, Any]]:
    """Select all subtitles or a caller-ordered subset."""

    if getall:
        return subtitles
    by_id = {str(subtitle["subtitle_id"]): subtitle for subtitle in subtitles}
    missing = [subtitle_id for subtitle_id in ids or () if subtitle_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Anime audio subtitle not found: {', '.join(missing)}",
        )
    return [by_id[subtitle_id] for subtitle_id in ids or ()]


def public_subtitle_content(
    connection: sqlite3.Connection,
    library_root: Path,
    subtitle: dict[str, Any],
) -> dict[str, Any]:
    """Return one subtitle as base64 JSON for the bulk endpoint."""

    try:
        path = resolve_content_path(connection, library_root, subtitle)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Indexed subtitle is unavailable") from error
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Indexed subtitle is unavailable")
    return {
        "subtitle": public_subtitle(subtitle),
        "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }
