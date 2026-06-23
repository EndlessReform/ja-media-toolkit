"""Canonical manifest persistence and Audiobookshelf metadata projection."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ja_media_core.audio_library import (
    AnimeAudioManifest,
)
from ja_media_core.audio_manifest import manifest_from_mapping, manifest_to_mapping


def load_manifest(path: Path) -> AnimeAudioManifest:
    """Load and validate the supported manifest schema."""

    try:
        return manifest_from_mapping(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as error:
        raise ValueError(f"unsupported anime audio manifest: {path}") from error


def write_manifest_atomic(path: Path, manifest: AnimeAudioManifest) -> None:
    """Write a complete JSON document and atomically replace the prior manifest."""

    _write_json_atomic(path, manifest_to_mapping(manifest))


def write_metadata_atomic(path: Path, manifest: AnimeAudioManifest) -> None:
    """Write the Audiobookshelf metadata projection atomically."""

    _write_json_atomic(path, project_audiobookshelf_metadata(manifest))


def project_audiobookshelf_metadata(
    manifest: AnimeAudioManifest,
) -> dict[str, object]:
    """Project authoritative series metadata into Audiobookshelf's fixed schema."""

    series = manifest.series
    tags = ["ja-media", "anime", f"anilist:{series.anilist_id}"]
    if series.mal_id is not None:
        tags.append(f"mal:{series.mal_id}")
    return {
        "title": series.title_preferred,
        "author": "Japanese Animation",
        "description": series.description_text or "",
        "releaseDate": _date_text(series.start_date),
        "genres": ["Anime", *series.genres],
        "tags": tags,
        "language": "ja",
        "explicit": False,
        "podcastType": "episodic",
    }


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _date_text(value) -> str | None:
    return value.isoformat() if value else None
