from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ja_media_core.crosswalk import anime_list_lookup_rows as lookup_rows
from ja_media_services.anime_crosswalk.db import (
    SCHEMA_VERSION,
    initialize_schema,
    validate_generated_db,
)


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
