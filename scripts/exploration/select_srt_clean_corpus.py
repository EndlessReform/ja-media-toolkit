#!/usr/bin/env -S uv run --project packages/frontend
"""Select a small, repeatable SRT-cleaning corpus slice.

The input is an exported ``canonical_episode_inputs`` JSONL snapshot. The
script checks only AniList series with canonical episode 1, queries their
Kitsunekko inventories, and chooses a mix of release-count buckets.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
from typing import Any

from ja_media_core import HttpKitsunekkoSubtitlesClient, media_filename


BUCKETS = (("single", 1, 1), ("few", 2, 3), ("many", 4, None))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-bucket", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=12)
    args = parser.parse_args()

    series_ids = canonical_episode_one_series(args.canonical)
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        rows = list(pool.map(inspect_series, series_ids))
    eligible = [row for row in rows if row["srt_count"]]
    selected = select_rows(eligible, args.per_bucket)

    payload = {
        "schema_name": "ja-media.srt-clean.corpus-slice",
        "schema_version": "1.0.0",
        "rules": {
            "episode": 1,
            "per_release_bucket": args.per_bucket,
            "release_buckets": [item[0] for item in BUCKETS],
        },
        "canonical_series_checked": len(series_ids),
        "overlap_series": len(eligible),
        "selected_series": len(selected),
        "selected_srt_files": sum(row["srt_count"] for row in selected),
        "selected": selected,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    ids_path = args.output.with_suffix(".anilist.txt")
    ids_path.write_text("".join(f"{row['anilist_id']}\n" for row in selected))
    print(f"output={args.output}")
    print(f"anilist_ids={ids_path}")
    print(f"canonical_series_checked={len(series_ids)}")
    print(f"overlap_series={len(eligible)}")
    print(f"selected_series={len(selected)}")
    print(f"selected_srt_files={payload['selected_srt_files']}")


def canonical_episode_one_series(path: Path) -> list[int]:
    ids: set[int] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("namespace") == "anilist" and str(row.get("episode")) == "1":
                ids.add(int(row["series_id"]))
    return sorted(ids)


def inspect_series(anilist_id: int) -> dict[str, Any]:
    response = HttpKitsunekkoSubtitlesClient(timeout_s=20).anilist_files(anilist_id)
    files = []
    for item in response.files:
        filename = str(item.get("filename") or item.get("name") or "")
        repo_path = str(item.get("repo_path") or "")
        if not (filename.lower().endswith(".srt") or repo_path.lower().endswith(".srt")):
            continue
        if media_filename.suggest_ordinary_episode(Path(filename).stem) != 1:
            continue
        files.append(
            {
                "subtitle_id": str(item.get("subtitle_id") or ""),
                "filename": filename or Path(repo_path).name,
                "repo_path": repo_path,
                "release_label": release_label(filename or Path(repo_path).name),
            }
        )
    labels = sorted({item["release_label"] for item in files})
    return {
        "anilist_id": anilist_id,
        "episode": 1,
        "bucket": release_bucket(len(files)),
        "srt_count": len(files),
        "distinct_release_labels": len(labels),
        "release_labels": labels,
        "files": files,
    }


def release_label(filename: str) -> str:
    match = re.match(r"\[([^]]+)]", filename)
    if match:
        return match.group(1).strip()
    match = re.match(r"\(([^)]+)\)", filename)
    if match:
        return match.group(1).strip()
    return "unlabelled"


def release_bucket(count: int) -> str | None:
    for name, minimum, maximum in BUCKETS:
        if count >= minimum and (maximum is None or count <= maximum):
            return name
    return None


def select_rows(rows: list[dict[str, Any]], per_bucket: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for name, _, _ in BUCKETS:
        candidates = [row for row in rows if row["bucket"] == name]
        candidates.sort(
            key=lambda row: (
                -row["distinct_release_labels"],
                -row["srt_count"],
                row["anilist_id"],
            )
        )
        selected.extend(candidates[:per_bucket])
    target = per_bucket * len(BUCKETS)
    selected_ids = {row["anilist_id"] for row in selected}
    backfill = [row for row in rows if row["anilist_id"] not in selected_ids]
    backfill.sort(
        key=lambda row: (
            -row["distinct_release_labels"],
            -row["srt_count"],
            row["anilist_id"],
        )
    )
    selected.extend(backfill[: max(0, target - len(selected))])
    return sorted(selected, key=lambda row: row["anilist_id"])


if __name__ == "__main__":
    main()
