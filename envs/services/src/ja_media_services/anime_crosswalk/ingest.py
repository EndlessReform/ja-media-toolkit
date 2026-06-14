from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ja_media_services.anime_crosswalk.db import (
    SCHEMA_VERSION,
    initialize_schema,
    validate_generated_db,
)


SOURCE_FIELDS = {
    "anidb_id": "anidb",
    "mal_id": "mal",
    "anilist_id": "anilist",
    "kitsu_id": "kitsu",
    "tvdb_id": "tvdb",
    "imdb_id": "imdb",
    "anime-planet_id": "anime-planet",
    "anisearch_id": "anisearch",
    "animenewsnetwork_id": "animenewsnetwork",
    "livechart_id": "livechart",
    "simkl_id": "simkl",
}


def scalar_ids(value: Any) -> Iterable[str]:
    """Yield normalized scalar IDs from upstream values.

    Upstream fields are usually single numbers or strings, but preserving a
    small iterable path makes ingestion tolerant of source-side shape changes.
    """

    if value is None or value == "":
        return
    if isinstance(value, list):
        for item in value:
            yield from scalar_ids(item)
        return
    yield str(value)


def infer_tvdb_kind(payload: dict[str, Any]) -> str:
    """Conservatively infer TVDB media kind from upstream row shape.

    Fribb/anime-lists gives TMDB explicit TV/movie slots but TVDB is less
    direct. Treat movie rows without a TVDB season marker as movies; everything
    else is series-like. Broad kindless TVDB lookup rows are also emitted, so
    this classification only affects callers that explicitly ask for a kind.
    """

    season = payload.get("season")
    tvdb_season = season.get("tvdb") if isinstance(season, dict) else None
    if payload.get("type") == "MOVIE" and tvdb_season in (None, 0, "0", ""):
        return "movie"
    return "tv"


def lookup_rows(payload: dict[str, Any], row_id: int) -> list[tuple[str, str, str | None, int]]:
    """Build lookup-table rows for one upstream anime-list object."""

    rows: list[tuple[str, str, str | None, int]] = []
    for field, source in SOURCE_FIELDS.items():
        for external_id in scalar_ids(payload.get(field)):
            rows.append((source, external_id, None, row_id))
            if source == "tvdb":
                rows.append((source, external_id, infer_tvdb_kind(payload), row_id))

    tmdb_ids = payload.get("themoviedb_id")
    if isinstance(tmdb_ids, dict):
        for media_kind in ("tv", "movie"):
            for external_id in scalar_ids(tmdb_ids.get(media_kind)):
                rows.append(("tmdb", external_id, media_kind, row_id))
                rows.append(("tmdb", external_id, None, row_id))
    else:
        for external_id in scalar_ids(tmdb_ids):
            rows.append(("tmdb", external_id, None, row_id))
    return rows


def build_database(
    *,
    input_path: Path,
    output_path: Path,
    source_repo: str,
    source_branch: str,
    source_commit: str,
) -> dict[str, str]:
    """Build a complete generated SQLite database from anime-list-full.json."""

    if output_path.exists():
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payloads = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payloads, list):
        raise ValueError("anime-list-full.json must contain a JSON array")

    connection = sqlite3.connect(output_path)
    try:
        initialize_schema(connection)
        lookup_counter: Counter[str] = Counter()
        lookup_count = 0

        for index, payload in enumerate(payloads, start=1):
            if not isinstance(payload, dict):
                raise ValueError(f"Anime row {index} is not an object")
            anidb_id = payload.get("anidb_id")
            connection.execute(
                "INSERT INTO anime (row_id, anidb_id, payload_json) VALUES (?, ?, ?)",
                (
                    index,
                    int(anidb_id) if isinstance(anidb_id, int | str) and str(anidb_id).isdigit() else None,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            rows = lookup_rows(payload, index)
            connection.executemany(
                """
                INSERT OR IGNORE INTO lookup
                (source, external_id, media_kind, row_id)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            for source, _external_id, media_kind, _row_id in rows:
                lookup_counter[source] += 1
                if media_kind is not None:
                    lookup_counter[f"{source}_{media_kind}"] += 1
            lookup_count += len(rows)

        metadata = {
            "source_repo": source_repo,
            "source_branch": source_branch,
            "source_commit": source_commit,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "anime_count": str(len(payloads)),
            "lookup_count": str(lookup_count),
            "tvdb_lookup_count": str(lookup_counter["tvdb"]),
            "tvdb_tv_lookup_count": str(lookup_counter["tvdb_tv"]),
            "tvdb_movie_lookup_count": str(lookup_counter["tvdb_movie"]),
            "mal_lookup_count": str(lookup_counter["mal"]),
            "anidb_lookup_count": str(lookup_counter["anidb"]),
            "tmdb_lookup_count": str(lookup_counter["tmdb"]),
            "tmdb_tv_lookup_count": str(lookup_counter["tmdb_tv"]),
            "tmdb_movie_lookup_count": str(lookup_counter["tmdb_movie"]),
            "schema_version": SCHEMA_VERSION,
        }
        connection.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        connection.commit()
    finally:
        connection.close()

    return validate_generated_db(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build anime crosswalk SQLite DB")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--source-commit", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metadata = build_database(
        input_path=args.input,
        output_path=args.output,
        source_repo=args.source_repo,
        source_branch=args.source_branch,
        source_commit=args.source_commit,
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))
