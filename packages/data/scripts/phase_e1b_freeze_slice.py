#!/usr/bin/env -S uv run
"""Freeze 3–5 current canonical episodes into the disposable E1-B MinIO area.

The configured DuckLake catalog and bronze bucket are read-only.  This script
downloads only the requested audio objects and writes only under
``phase-e1b/`` in the local development MinIO bucket.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
from typing import Any

from ja_media_data.environment import load_cli_environment
from ja_media_data.lakehouse.repository import repository_from_env
from ja_media_data.orchestration.dagster.e1b_storage import SELECTION_KEY, SpikeStore
from ja_media_data.storage.bronze import BronzeStore, bronze_store_from_env


def main() -> None:
    load_cli_environment()
    args = _arguments()
    namespace, series_id = _series(args.series)
    episodes = tuple(_episode(value) for value in args.episodes)
    rows = _canonical_rows(namespace, series_id, episodes)
    source = bronze_store_from_env()
    local = SpikeStore()
    with tempfile.TemporaryDirectory(prefix="ja-media-e1b-freeze-") as directory:
        staged = [
            _stage_source(source, Path(directory), row)
            for row in rows
        ]
        selection_fp = _fingerprint(staged)
        entries = [
            _upload_source(local, selection_fp, item)
            for item in staged
        ]
    selection = {
        "schema_version": 1,
        "selection_fingerprint": selection_fp,
        "series": f"{namespace}:{series_id}",
        "episodes": entries,
    }
    local.put_json(SELECTION_KEY, selection)
    print(
        json.dumps(
            {
                "selection_key": SELECTION_KEY,
                "selection_fingerprint": selection_fp,
                "locators": [item["locator"] for item in entries],
                "input_bytes": sum(int(item["bytes"]) for item in entries),
                "duration_seconds": round(
                    sum(float(item["duration_seconds"]) for item in entries), 3
                ),
            },
            sort_keys=True,
        )
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", default="anilist:10087")
    parser.add_argument("--episodes", nargs="+", default=("01", "02", "03"))
    args = parser.parse_args()
    if not 3 <= len(args.episodes) <= 5:
        parser.error("--episodes requires three to five values")
    return args


def _series(value: str) -> tuple[str, str]:
    try:
        namespace, identifier = value.split(":", 1)
    except ValueError as error:
        raise SystemExit("--series must be namespace:id") from error
    if not namespace or not identifier:
        raise SystemExit("--series must be namespace:id")
    return namespace, identifier


def _episode(value: str) -> str:
    try:
        number = int(value)
    except ValueError as error:
        raise SystemExit(f"episode must be an integer: {value}") from error
    if number < 1:
        raise SystemExit("episodes must be positive")
    return str(number)


def _canonical_rows(
    namespace: str, series_id: str, episodes: tuple[str, ...]
) -> list[dict[str, object]]:
    placeholders = ",".join("?" for _ in episodes)
    with repository_from_env(ensure_schema=False) as repository:
        rows = repository.connection.execute(
            f"""SELECT namespace, series_id, episode, audio_capture_id,
                       manifest_bucket, manifest_key, manifest_etag,
                       input_fingerprint
                  FROM canonical_episode_inputs
                 WHERE namespace = ? AND series_id = ?
                   AND episode IN ({placeholders})""",
            [namespace, series_id, *episodes],
        ).fetchall()
    by_episode = {str(row[2]): row for row in rows}
    missing = [episode for episode in episodes if episode not in by_episode]
    if missing:
        raise RuntimeError(f"episodes are not canonical: {', '.join(missing)}")
    if len(rows) != len(episodes):
        raise RuntimeError("canonical query returned duplicate episode rows")
    return [
        dict(
            zip(
                (
                    "namespace", "series_id", "episode", "capture_id",
                    "manifest_bucket", "manifest_key", "manifest_etag",
                    "canonical_fingerprint",
                ),
                by_episode[episode],
                strict=True,
            )
        )
        for episode in episodes
    ]


def _stage_source(
    source: BronzeStore, directory: Path, row: dict[str, object]
) -> dict[str, object]:
    manifest_key = str(row["manifest_key"])
    manifest = source.read_manifest(
        manifest_key, expected_etag=str(row["manifest_etag"])
    )
    audio = manifest.get("audio")
    if not isinstance(audio, dict):
        raise RuntimeError(f"manifest has no audio object: {manifest_key}")
    source_key = _audio_key(source.prefix, manifest_key, audio)
    response = source._client.get_object(Bucket=source.bucket, Key=source_key)
    audio_name = PurePosixPath(source_key).name
    path = directory / str(row["episode"]) / audio_name
    path.parent.mkdir(parents=True)
    with path.open("wb") as output:
        shutil.copyfileobj(response["Body"], output)
    size = path.stat().st_size
    if size < 1:
        raise RuntimeError(f"source audio is empty: {source_key}")
    return row | {
        "source_key": source_key,
        "source_etag": str(response.get("ETag", "")).strip('"'),
        "audio_name": audio_name,
        "bytes": size,
        "duration_seconds": _duration(path),
        "path": path,
    }


def _audio_key(prefix: str, manifest_key: str, audio: dict[str, Any]) -> str:
    raw = audio.get("key") or audio.get("filename")
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError(f"manifest has no audio key: {manifest_key}")
    candidate = PurePosixPath(raw.strip())
    series_root = PurePosixPath(manifest_key).parent.parent
    if str(candidate).startswith(prefix.rstrip("/") + "/"):
        return str(candidate)
    return str(series_root / candidate)


def _duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(result.stdout.strip())
    if duration < 60:
        raise RuntimeError(f"E1-B requires full episode audio, found {duration:.1f}s")
    return duration


def _fingerprint(entries: list[dict[str, object]]) -> str:
    durable = [
        {key: str(item[key]) for key in (
            "namespace", "series_id", "episode", "capture_id", "manifest_key",
            "manifest_etag", "canonical_fingerprint", "source_key", "source_etag",
            "bytes",
        )}
        for item in entries
    ]
    return hashlib.sha256(json.dumps(durable, sort_keys=True).encode()).hexdigest()


def _upload_source(
    local: SpikeStore, selection_fp: str, staged: dict[str, object]
) -> dict[str, object]:
    local_key = (
        f"phase-e1b/source/{selection_fp}/{staged['capture_id']}/"
        f"{staged['audio_name']}"
    )
    uploaded = local.upload_file(local_key, Path(str(staged["path"])))
    return {
        key: staged[key]
        for key in (
            "capture_id", "manifest_key", "manifest_etag",
            "canonical_fingerprint", "source_key", "source_etag", "audio_name",
            "bytes", "duration_seconds",
        )
    } | {
        "locator": (
            f"{staged['namespace']}:{staged['series_id']}:"
            f"{int(str(staged['episode'])):02d}"
        ),
        "local_key": local_key,
        "local_etag": uploaded["etag"],
    }


if __name__ == "__main__":
    main()
