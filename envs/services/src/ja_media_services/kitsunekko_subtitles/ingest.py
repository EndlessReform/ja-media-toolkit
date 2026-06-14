from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ja_media_services.kitsunekko_subtitles.db import (
    SCHEMA_VERSION,
    initialize_schema,
    validate_generated_db,
)


SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa"}
SXXEYY_RE = re.compile(r"(?i)\bS(?P<season>\d{1,2})E(?P<episode>\d{1,4})\b")
EPISODE_WORD_RE = re.compile(
    r"(?ix)\b(?:episode|ep|eps|話|第)\s*[._ -]?\s*(?P<episode>\d{1,4})"
)
BARE_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])(?P<number>\d{1,4})(?![A-Za-z0-9])")
LEADING_GROUP_RE = re.compile(r"^\[(?P<group>[^\]]+)\]")
BRACKET_RE = re.compile(r"\[(?P<tag>[^\]]+)\]")
LANG_SUFFIX_RE = re.compile(
    r"(?i)\.(?P<lang>[a-z]{2,3}(?:-[a-z0-9]+)?)(?:\[[^\]]+\])?\.(?:srt|ass|ssa)$"
)
IGNORED_BARE_NUMBERS = {264, 265, 480, 540, 576, 720, 1080, 1280, 1920, 2160}
SUBTITLE_ID_NAMESPACE = uuid.UUID("4b7dc809-92d0-597b-bd44-2f6e5b13bd2a")
BATCH_SIZE = 5000


@dataclass(frozen=True)
class EpisodeGuess:
    """A conservative filename episode parse plus audit context."""

    local: int | None
    absolute: int | None
    raw: str | None
    confidence: str


@dataclass(frozen=True)
class SubtitleCandidate:
    """One subtitle file discovered under a Kitsunekko title directory."""

    anilist_id: int
    repo_path: str
    filename: str
    extension: str
    episode: EpisodeGuess
    group_hint: str | None
    language_hint: str
    release_tags: tuple[str, ...]
    last_modified: str | None


def connect_crosswalk(crosswalk_db_path: Path) -> sqlite3.Connection:
    """Open the generated anime crosswalk DB read-only."""

    if not crosswalk_db_path.exists():
        raise FileNotFoundError(f"Crosswalk DB does not exist: {crosswalk_db_path}")
    uri = f"file:{crosswalk_db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def read_crosswalk_metadata(crosswalk_db_path: Path) -> dict[str, str]:
    """Read source metadata from the generated anime crosswalk database."""

    connection = connect_crosswalk(crosswalk_db_path)
    try:
        return {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM metadata ORDER BY key")
        }
    finally:
        connection.close()


def coerce_int(value: Any) -> int | None:
    """Return a positive integer ID from tolerant JSON metadata."""

    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def split_prefixed_id(value: Any) -> tuple[str | None, str | None]:
    """Parse Kitsunekko metadata values like ``movie:433945``."""

    if value is None:
        return None, None
    raw = str(value).strip()
    if not raw:
        return None, None
    if ":" not in raw:
        return None, raw
    prefix, external_id = raw.split(":", 1)
    media_kind = prefix.strip().lower() or None
    return media_kind, external_id.strip() or None


def anilist_ids_from_lookup(
    crosswalk: sqlite3.Connection,
    *,
    source: str,
    external_id: str,
    media_kind: str | None = None,
) -> set[int]:
    """Resolve a source ID to AniList IDs using the generated crosswalk DB."""

    rows = crosswalk.execute(
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

    anilist_ids: set[int] = set()
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        anilist_id = coerce_int(payload.get("anilist_id"))
        if anilist_id is not None:
            anilist_ids.add(anilist_id)
    return anilist_ids


def anilist_ids_for_metadata(metadata: dict[str, Any], crosswalk: sqlite3.Connection) -> set[int]:
    """Return AniList IDs represented by one Kitsunekko title metadata file."""

    direct_id = coerce_int(metadata.get("anilist_id"))
    if direct_id is not None:
        return {direct_id}

    resolved: set[int] = set()
    for source, field in (("tvdb", "tvdb_id"), ("tmdb", "tmdb_id"), ("mal", "mal_id")):
        media_kind, external_id = split_prefixed_id(metadata.get(field))
        if external_id is None:
            continue
        resolved.update(
            anilist_ids_from_lookup(
                crosswalk,
                source=source,
                external_id=external_id,
                media_kind=media_kind if source in {"tvdb", "tmdb"} else None,
            )
        )
    if len(resolved) > 1:
        return set()
    return resolved


def parse_episode(filename: str) -> EpisodeGuess:
    """Conservatively parse one subtitle filename for a local episode number."""

    sxxeyy_match = SXXEYY_RE.search(filename)
    if sxxeyy_match:
        return EpisodeGuess(
            local=int(sxxeyy_match.group("episode")),
            absolute=None,
            raw=sxxeyy_match.group(0),
            confidence="high",
        )

    word_match = EPISODE_WORD_RE.search(filename)
    if word_match:
        return EpisodeGuess(
            local=int(word_match.group("episode")),
            absolute=None,
            raw=word_match.group(0),
            confidence="medium",
        )

    for match in BARE_NUMBER_RE.finditer(filename):
        number = int(match.group("number"))
        before = filename[max(0, match.start() - 2) : match.start()].lower()
        after = filename[match.end() : match.end() + 2].lower()
        if number in IGNORED_BARE_NUMBERS or 1900 <= number <= 2099:
            continue
        if before.endswith("x") or after.startswith("p"):
            continue
        if 0 < number <= 1500:
            return EpisodeGuess(
                local=number,
                absolute=None,
                raw=match.group("number"),
                confidence="medium" if number <= 250 else "low",
            )

    return EpisodeGuess(local=None, absolute=None, raw=None, confidence="none")


def group_hint(filename: str) -> str | None:
    """Return the leading bracket release group when present."""

    match = LEADING_GROUP_RE.search(filename)
    if not match:
        return None
    group = re.sub(r"\s+", " ", match.group("group")).strip()
    return group or None


def language_hint(filename: str) -> str:
    """Infer a small language hint from filename suffixes and bracket tags."""

    suffix_match = LANG_SUFFIX_RE.search(filename)
    if suffix_match:
        return suffix_match.group("lang").lower()

    for tag in (tag.lower() for tag in BRACKET_RE.findall(filename)):
        if "jpn" in tag or "japanese" in tag:
            return "jpn"
        if re.search(r"\bjp\b|\bja\b", tag):
            return "ja"
        if "chs" in tag or "cht" in tag:
            return "zh"
        if "eng" in tag or re.search(r"\ben\b", tag):
            return "en"
    return "unknown"


def iter_subtitle_candidates(mirror_dir: Path, crosswalk: sqlite3.Connection) -> Iterable[SubtitleCandidate]:
    """Yield subtitle files under the Kitsunekko mirror with AniList IDs."""

    subtitles_root = mirror_dir / "subtitles"
    if not subtitles_root.exists():
        raise FileNotFoundError(f"Kitsunekko subtitles directory does not exist: {subtitles_root}")

    for info_path in sorted(subtitles_root.glob("*/*/.kitsuinfo.json")):
        try:
            metadata = json.loads(info_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid Kitsunekko metadata JSON: {info_path}") from error
        if not isinstance(metadata, dict):
            raise ValueError(f"Kitsunekko metadata is not an object: {info_path}")

        anilist_ids = anilist_ids_for_metadata(metadata, crosswalk)
        if not anilist_ids:
            continue

        title_dir = info_path.parent
        last_modified = str(metadata.get("last_modified") or "") or None
        for file_path in sorted(title_dir.iterdir()):
            if not file_path.is_file():
                continue
            extension = file_path.suffix.lower()
            if extension not in SUBTITLE_EXTENSIONS:
                continue
            repo_path = file_path.relative_to(mirror_dir).as_posix()
            filename = file_path.name
            episode = parse_episode(filename)
            tags = tuple(BRACKET_RE.findall(filename))
            for anilist_id in sorted(anilist_ids):
                yield SubtitleCandidate(
                    anilist_id=anilist_id,
                    repo_path=repo_path,
                    filename=filename,
                    extension=extension.lstrip("."),
                    episode=episode,
                    group_hint=group_hint(filename),
                    language_hint=language_hint(filename),
                    release_tags=tags,
                    last_modified=last_modified,
                )


def subtitle_uuid(repo_path: str) -> str:
    """Return a stable UUID for one mirror-relative subtitle path."""

    return str(uuid.uuid5(SUBTITLE_ID_NAMESPACE, repo_path))


def subtitle_insert_row(subtitle: SubtitleCandidate) -> tuple[Any, ...]:
    """Convert a subtitle candidate to a SQLite insert row."""

    return (
        subtitle_uuid(subtitle.repo_path),
        subtitle.anilist_id,
        subtitle.repo_path,
        subtitle.filename,
        subtitle.extension,
        subtitle.episode.local,
        subtitle.episode.absolute,
        subtitle.episode.raw,
        subtitle.episode.confidence,
        subtitle.group_hint,
        subtitle.language_hint,
        json.dumps(list(subtitle.release_tags), ensure_ascii=False, separators=(",", ":")),
        subtitle.last_modified,
    )


def flush_subtitle_rows(connection: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    """Batch insert buffered subtitle rows."""

    if not rows:
        return
    connection.executemany(
        """
        INSERT INTO subtitle_file (
          subtitle_id,
          anilist_id,
          repo_path,
          filename,
          extension,
          episode_local,
          episode_absolute,
          episode_raw,
          episode_confidence,
          group_hint,
          language_hint,
          release_tags_json,
          last_modified
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    rows.clear()


def build_database(
    *,
    output_path: Path,
    crosswalk_db_path: Path,
    mirror_dir: Path,
    mirror_repo: str,
    mirror_branch: str,
    mirror_commit: str,
) -> dict[str, str]:
    """Build the generated subtitle index from the mirror and crosswalk DB."""

    if output_path.exists():
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    crosswalk = connect_crosswalk(crosswalk_db_path)
    connection = sqlite3.connect(output_path)
    try:
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA temp_store = MEMORY")
        crosswalk_metadata = {
            str(row["key"]): str(row["value"])
            for row in crosswalk.execute("SELECT key, value FROM metadata ORDER BY key")
        }
        initialize_schema(connection)
        subtitle_count = 0
        pending_rows: list[tuple[Any, ...]] = []
        started_at = time.monotonic()
        last_progress_at = started_at

        for subtitle in iter_subtitle_candidates(mirror_dir, crosswalk):
            pending_rows.append(subtitle_insert_row(subtitle))
            subtitle_count += 1
            if len(pending_rows) >= BATCH_SIZE:
                flush_subtitle_rows(connection, pending_rows)

            now = time.monotonic()
            if subtitle_count == 1 or subtitle_count % 10000 == 0 or now - last_progress_at >= 15:
                elapsed_s = max(now - started_at, 0.001)
                print(
                    "kitsunekko-subtitles-ingest: indexed "
                    f"{subtitle_count} subtitle rows in {elapsed_s:.1f}s "
                    f"({subtitle_count / elapsed_s:.1f} rows/s)",
                    file=sys.stderr,
                    flush=True,
                )
                last_progress_at = now

        flush_subtitle_rows(connection, pending_rows)
        print(
            "kitsunekko-subtitles-ingest: finished scan with "
            f"{subtitle_count} subtitle rows",
            file=sys.stderr,
            flush=True,
        )
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "mirror_repo": mirror_repo,
            "mirror_branch": mirror_branch,
            "mirror_commit": mirror_commit,
            "crosswalk_db_path": str(crosswalk_db_path),
            "crosswalk_source_repo": crosswalk_metadata.get("source_repo", ""),
            "crosswalk_source_branch": crosswalk_metadata.get("source_branch", ""),
            "crosswalk_source_commit": crosswalk_metadata.get("source_commit", ""),
            "crosswalk_schema_version": crosswalk_metadata.get("schema_version", ""),
            "subtitle_row_count": str(subtitle_count),
            "lookup_row_count": "0",
            "ingest_phase": "indexed",
        }
        connection.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        connection.commit()
    finally:
        connection.close()
        crosswalk.close()

    return validate_generated_db(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Kitsunekko subtitle SQLite DB")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--crosswalk-db", required=True, type=Path)
    parser.add_argument("--mirror-dir", required=True, type=Path)
    parser.add_argument("--mirror-repo", required=True)
    parser.add_argument("--mirror-branch", required=True)
    parser.add_argument("--mirror-commit", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metadata = build_database(
        output_path=args.output,
        crosswalk_db_path=args.crosswalk_db,
        mirror_dir=args.mirror_dir,
        mirror_repo=args.mirror_repo,
        mirror_branch=args.mirror_branch,
        mirror_commit=args.mirror_commit,
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))
