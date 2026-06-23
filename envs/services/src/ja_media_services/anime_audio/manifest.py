"""Parse and validate one authoritative anime-audio manifest for indexing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ja_media_core.audio_manifest import manifest_from_mapping
from ja_media_services.anime_audio.db import artifact_path


@dataclass(frozen=True)
class IndexedManifest:
    """SQLite-ready rows plus the source manifest metadata."""

    series_row: tuple[Any, ...]
    artifact_rows: tuple[tuple[Any, ...], ...]


def load_manifest(root: Path, path: Path) -> IndexedManifest:
    """Read, validate, and convert one immediate-child manifest."""

    stat = path.stat()
    manifest = manifest_from_mapping(json.loads(path.read_text(encoding="utf-8")))
    relative = relative_manifest(root, path)
    series_row = (
        manifest.series.anilist_id,
        manifest.series.title_preferred,
        manifest.series.title_english,
        manifest.series.title_native,
        manifest.series.title_romaji,
        manifest.profile.name,
        relative,
        stat.st_mtime_ns,
        stat.st_size,
    )
    artifacts = []
    for episode in manifest.episodes:
        resolved = artifact_path(path.parent, episode.artifact.relative_path)
        if not resolved.is_file():
            raise FileNotFoundError(
                f"artifact missing for episode {episode.episode_key}"
            )
        artifacts.append(
            (
                manifest.series.anilist_id,
                episode.episode_key,
                manifest.profile.name,
                episode.artifact.relative_path,
                episode.artifact.size_bytes,
                episode.artifact.duration_ms,
                episode.artifact.codec,
                episode.artifact.bitrate_bps,
                episode.artifact.channels,
                episode.artifact.sample_rate_hz,
                episode.artifact.sha256,
                episode.created_at,
            )
        )
    return IndexedManifest(series_row, tuple(artifacts))


def relative_manifest(root: Path, path: Path) -> str:
    """Return the durable relative identity for an immediate-child manifest."""

    relative = path.relative_to(root)
    if len(relative.parts) != 2 or relative.name != ".ja-media.json":
        raise ValueError("manifest must be an immediate series child")
    return relative.as_posix()


def error_row(
    path: Path, relative: str, code: str, error: Exception
) -> tuple[str, str, str, int | None, int | None]:
    """Keep failed-manifest metadata so unchanged failures are not reparsed."""

    try:
        stat = path.stat()
        return relative, code, str(error), stat.st_mtime_ns, stat.st_size
    except OSError:
        return relative, code, str(error), None, None
