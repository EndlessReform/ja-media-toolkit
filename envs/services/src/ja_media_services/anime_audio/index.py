"""Rebuildable SQLite index over authoritative anime-audio manifests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ja_media_core.audio_manifest import manifest_from_mapping
from ja_media_services.anime_audio.db import (
    artifact_path,
    count_rows,
    set_metadata,
)


@dataclass(frozen=True)
class ReconciliationResult:
    """Bounded operational facts from one complete library scan."""

    status: str
    series_count: int
    artifact_count: int
    error_count: int
    completed_at: str


def reconcile(connection: sqlite3.Connection, library_root: Path) -> ReconciliationResult:
    """Scan all immediate series manifests and atomically replace the index."""

    completed_at = datetime.now(timezone.utc).isoformat()
    root = library_root.resolve()
    if not root.is_dir():
        _record_failed_attempt(connection, completed_at, "library_unavailable")
        raise FileNotFoundError("anime audio library root is unavailable")

    series_rows: list[tuple[Any, ...]] = []
    artifact_rows: list[tuple[Any, ...]] = []
    errors: list[tuple[str, str, str]] = []
    seen_ids: set[int] = set()
    for manifest_path in sorted(root.glob("*/.ja-media.json")):
        relative_manifest = manifest_path.relative_to(root).as_posix()
        try:
            manifest = manifest_from_mapping(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
            anilist_id = manifest.series.anilist_id
            if anilist_id in seen_ids:
                raise ValueError(f"duplicate AniList ID {anilist_id}")
            seen_ids.add(anilist_id)
            series_rows.append(
                (
                    anilist_id,
                    manifest.series.title_preferred,
                    manifest.series.title_english,
                    manifest.series.title_native,
                    manifest.series.title_romaji,
                    manifest.profile.name,
                    relative_manifest,
                )
            )
            for episode in manifest.episodes:
                resolved_artifact = artifact_path(
                    manifest_path.parent, episode.artifact.relative_path
                )
                if not resolved_artifact.is_file():
                    raise FileNotFoundError(
                        f"artifact missing for episode {episode.episode_key}"
                    )
                artifact_rows.append(
                    (
                        anilist_id,
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
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append((relative_manifest, "invalid_manifest", str(error)))
            _discard_series_rows(series_rows, artifact_rows, manifest_path, root)
        except FileNotFoundError as error:
            errors.append((relative_manifest, "missing_artifact", str(error)))
            _discard_series_rows(series_rows, artifact_rows, manifest_path, root)
        except OSError as error:
            errors.append((relative_manifest, "filesystem_error", str(error)))
            _discard_series_rows(series_rows, artifact_rows, manifest_path, root)

    with connection:
        connection.execute("DELETE FROM artifact")
        connection.execute("DELETE FROM series")
        connection.execute("DELETE FROM reconciliation_error")
        connection.executemany(
            """
            INSERT INTO series(
              anilist_id, title, title_english, title_native, title_romaji,
              profile, manifest_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            series_rows,
        )
        connection.executemany(
            """
            INSERT INTO artifact(
              anilist_id, episode_key, profile, relative_path, size_bytes,
              duration_ms, codec, bitrate_bps, channels, sample_rate_hz,
              sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            artifact_rows,
        )
        connection.executemany(
            "INSERT INTO reconciliation_error VALUES (?, ?, ?)",
            errors,
        )
        set_metadata(connection, "last_attempt", completed_at)
        set_metadata(connection, "last_success", completed_at)
        set_metadata(connection, "last_failure_code", "")
        set_metadata(connection, "ready", "1")

    return ReconciliationResult(
        status="degraded" if errors else "ok",
        series_count=len(series_rows),
        artifact_count=len(artifact_rows),
        error_count=len(errors),
        completed_at=completed_at,
    )


def _discard_series_rows(
    series_rows: list[tuple[Any, ...]],
    artifact_rows: list[tuple[Any, ...]],
    manifest_path: Path,
    library_root: Path,
) -> None:
    relative_manifest = manifest_path.relative_to(library_root).as_posix()
    ids = {int(row[0]) for row in series_rows if row[-1] == relative_manifest}
    series_rows[:] = [row for row in series_rows if row[-1] != relative_manifest]
    artifact_rows[:] = [row for row in artifact_rows if int(row[0]) not in ids]


def _record_failed_attempt(connection: sqlite3.Connection, attempted_at: str, code: str) -> None:
    with connection:
        set_metadata(connection, "last_attempt", attempted_at)
        set_metadata(connection, "last_failure_code", code)
        if count_rows(connection, "series") == 0:
            set_metadata(connection, "ready", "0")
