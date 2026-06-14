from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ja_media_services.kitsunekko_subtitles.db import (
    SCHEMA_VERSION,
    initialize_schema,
    validate_generated_db,
)


def read_crosswalk_metadata(crosswalk_db_path: Path) -> dict[str, str]:
    """Read source metadata from the generated anime crosswalk database."""

    if not crosswalk_db_path.exists():
        raise FileNotFoundError(f"Crosswalk DB does not exist: {crosswalk_db_path}")

    uri = f"file:{crosswalk_db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM metadata ORDER BY key")
        }
    finally:
        connection.close()


def build_database(
    *,
    output_path: Path,
    crosswalk_db_path: Path,
    mirror_repo: str,
    mirror_branch: str,
    mirror_commit: str,
) -> dict[str, str]:
    """Build the generated subtitle DB shell from the mounted crosswalk DB.

    This is intentionally a ceremony builder, not the subtitle indexer. It
    proves the service can consume the crosswalk database read-only and publish
    source-version metadata before Kitsunekko mirror cloning is introduced.
    """

    if output_path.exists():
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    crosswalk_metadata = read_crosswalk_metadata(crosswalk_db_path)
    connection = sqlite3.connect(output_path)
    try:
        initialize_schema(connection)
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
            "subtitle_row_count": "0",
            "lookup_row_count": "0",
            "ingest_phase": "crosswalk-only",
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
    parser = argparse.ArgumentParser(description="Build Kitsunekko subtitle SQLite DB")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--crosswalk-db", required=True, type=Path)
    parser.add_argument("--mirror-repo", required=True)
    parser.add_argument("--mirror-branch", required=True)
    parser.add_argument("--mirror-commit", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metadata = build_database(
        output_path=args.output,
        crosswalk_db_path=args.crosswalk_db,
        mirror_repo=args.mirror_repo,
        mirror_branch=args.mirror_branch,
        mirror_commit=args.mirror_commit,
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))
