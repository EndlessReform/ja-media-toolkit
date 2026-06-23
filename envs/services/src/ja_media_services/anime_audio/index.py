"""Transactional indexing over authoritative anime-audio manifests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ja_media_services.anime_audio.db import (
    count_rows,
    increment_metadata,
    set_metadata,
)
from ja_media_services.anime_audio.manifest import (
    IndexedManifest,
    error_row,
    load_manifest,
    relative_manifest,
)


@dataclass(frozen=True)
class ReconciliationResult:
    """Bounded operational facts from one complete library scan."""

    status: str
    series_count: int
    artifact_count: int
    error_count: int
    completed_at: str


@dataclass(frozen=True)
class IncrementalScanResult:
    """Facts from one metadata-only comparison with the current index."""

    refreshed_count: int
    removed_count: int
    unchanged_count: int
    error_count: int
    completed_at: str


def reconcile(connection: sqlite3.Connection, library_root: Path) -> ReconciliationResult:
    """Scan immediate manifests and atomically replace the complete index."""

    completed_at = _now()
    root = _available_root(connection, library_root, completed_at)
    manifests: list[IndexedManifest] = []
    errors: list[tuple[str, str, str, int | None, int | None]] = []
    seen_ids: set[int] = set()
    for manifest_path in _manifest_paths(root):
        relative = relative_manifest(root, manifest_path)
        try:
            indexed = load_manifest(root, manifest_path)
            anilist_id = int(indexed.series_row[0])
            if anilist_id in seen_ids:
                raise ValueError(f"duplicate AniList ID {anilist_id}")
            seen_ids.add(anilist_id)
            manifests.append(indexed)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(error_row(manifest_path, relative, "invalid_manifest", error))
        except FileNotFoundError as error:
            errors.append(error_row(manifest_path, relative, "missing_artifact", error))
        except OSError as error:
            errors.append(error_row(manifest_path, relative, "filesystem_error", error))

    with connection:
        connection.execute("DELETE FROM artifact")
        connection.execute("DELETE FROM series")
        connection.execute("DELETE FROM reconciliation_error")
        connection.executemany(_SERIES_INSERT, [item.series_row for item in manifests])
        connection.executemany(
            _ARTIFACT_INSERT,
            [row for item in manifests for row in item.artifact_rows],
        )
        connection.executemany(
            "INSERT INTO reconciliation_error VALUES (?, ?, ?, ?, ?)", errors
        )
        set_metadata(connection, "last_attempt", completed_at)
        set_metadata(connection, "last_success", completed_at)
        set_metadata(connection, "last_failure_code", "")
        set_metadata(connection, "ready", "1")

    return ReconciliationResult(
        status="degraded" if errors else "ok",
        series_count=len(manifests),
        artifact_count=sum(len(item.artifact_rows) for item in manifests),
        error_count=len(errors),
        completed_at=completed_at,
    )


def refresh_manifest(
    connection: sqlite3.Connection, library_root: Path, manifest_path: Path
) -> bool:
    """Replace or remove exactly one manifest's rows in a transaction."""

    root = library_root.resolve()
    path = manifest_path.resolve()
    relative = relative_manifest(root, path)
    if not path.is_file():
        with connection:
            _remove_manifest(connection, relative)
        return True

    try:
        indexed = load_manifest(root, path)
        conflict = connection.execute(
            """
            SELECT manifest_path FROM series
            WHERE anilist_id = ? AND manifest_path != ?
            """,
            (indexed.series_row[0], relative),
        ).fetchone()
        if conflict:
            raise ValueError(f"duplicate AniList ID {indexed.series_row[0]}")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        _record_manifest_error(connection, path, relative, "invalid_manifest", error)
        return False
    except FileNotFoundError as error:
        _record_manifest_error(connection, path, relative, "missing_artifact", error)
        return False
    except OSError as error:
        _record_manifest_error(connection, path, relative, "filesystem_error", error)
        return False

    with connection:
        _remove_manifest(connection, relative)
        connection.execute(_SERIES_INSERT, indexed.series_row)
        connection.executemany(_ARTIFACT_INSERT, indexed.artifact_rows)
    return True


def incremental_scan(
    connection: sqlite3.Connection, library_root: Path
) -> IncrementalScanResult:
    """Repair missed events using manifest metadata, not artifact contents."""

    completed_at = _now()
    root = library_root.resolve()
    if not root.is_dir():
        with connection:
            increment_metadata(connection, "incremental_scan_failures")
            set_metadata(connection, "last_failure_code", "library_unavailable")
        raise FileNotFoundError("anime audio library root is unavailable")

    known = {
        str(row["manifest_path"]): (
            row["manifest_mtime_ns"],
            row["manifest_size"],
        )
        for row in connection.execute(
            "SELECT manifest_path, manifest_mtime_ns, manifest_size FROM series"
        )
    }
    known.update(
        {
            str(row["manifest_path"]): (
                row["manifest_mtime_ns"],
                row["manifest_size"],
            )
            for row in connection.execute(
                """
                SELECT manifest_path, manifest_mtime_ns, manifest_size
                FROM reconciliation_error
                """
            )
        }
    )
    current: set[str] = set()
    refreshed = unchanged = errors = 0
    for path in _manifest_paths(root):
        relative = relative_manifest(root, path)
        current.add(relative)
        stat = path.stat()
        if known.get(relative) == (stat.st_mtime_ns, stat.st_size):
            unchanged += 1
            continue
        refreshed += 1
        if not refresh_manifest(connection, root, path):
            errors += 1

    removed = 0
    for relative in sorted(set(known) - current):
        removed += 1
        refresh_manifest(connection, root, root / relative)

    with connection:
        set_metadata(connection, "last_incremental_scan", completed_at)
        set_metadata(connection, "last_failure_code", "")
    return IncrementalScanResult(
        refreshed_count=refreshed,
        removed_count=removed,
        unchanged_count=unchanged,
        error_count=errors,
        completed_at=completed_at,
    )


def _record_manifest_error(
    connection: sqlite3.Connection,
    path: Path,
    relative: str,
    code: str,
    error: Exception,
) -> None:
    with connection:
        _remove_manifest(connection, relative)
        connection.execute(
            "INSERT INTO reconciliation_error VALUES (?, ?, ?, ?, ?)",
            error_row(path, relative, code, error),
        )
        increment_metadata(connection, "refresh_failures")


def _remove_manifest(connection: sqlite3.Connection, relative: str) -> None:
    connection.execute("DELETE FROM series WHERE manifest_path = ?", (relative,))
    connection.execute(
        "DELETE FROM reconciliation_error WHERE manifest_path = ?", (relative,)
    )


def _available_root(
    connection: sqlite3.Connection, library_root: Path, attempted_at: str
) -> Path:
    root = library_root.resolve()
    if root.is_dir():
        return root
    with connection:
        set_metadata(connection, "last_attempt", attempted_at)
        set_metadata(connection, "last_failure_code", "library_unavailable")
        if count_rows(connection, "series") == 0:
            set_metadata(connection, "ready", "0")
    raise FileNotFoundError("anime audio library root is unavailable")


def _manifest_paths(root: Path) -> list[Path]:
    return sorted(root.glob("*/.ja-media.json"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SERIES_INSERT = """
INSERT INTO series(
  anilist_id, title, title_english, title_native, title_romaji, profile,
  manifest_path, manifest_mtime_ns, manifest_size
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_ARTIFACT_INSERT = """
INSERT INTO artifact(
  anilist_id, episode_key, profile, relative_path, size_bytes, duration_ms,
  codec, bitrate_bps, channels, sample_rate_hz, sha256, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
