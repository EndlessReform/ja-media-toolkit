"""Private command line for the subtitle-alignment experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from subtitle_alignment.snapshot import build_snapshot


def main() -> None:
    """Run one research operation without expanding the public ja-media CLI."""

    parser = argparse.ArgumentParser(prog="alignment-research")
    commands = parser.add_subparsers(dest="command", required=True)

    snapshot = commands.add_parser("snapshot", help="freeze a Phase 0 dataset")
    snapshot.add_argument("--series-count", type=int, default=25)
    snapshot.add_argument("--seed", type=int, default=20260726)
    snapshot.add_argument(
        "--cache-root",
        type=Path,
        default=Path(".cache"),
        help="local immutable dataset directory",
    )
    snapshot.add_argument("--download-workers", type=int, default=8)

    inspect = commands.add_parser("inspect", help="summarize a local dataset")
    inspect.add_argument("dataset", type=Path)

    args = parser.parse_args()
    if args.command == "snapshot":
        if args.series_count < 1:
            parser.error("--series-count must be positive")
        if not 1 <= args.download_workers <= 32:
            parser.error("--download-workers must be between 1 and 32")
        path = build_snapshot(
            cache_root=args.cache_root,
            series_count=args.series_count,
            seed=args.seed,
            download_workers=args.download_workers,
        )
        print(f"dataset={path}")
        return

    manifest = args.dataset.expanduser().resolve() / "manifest.json"
    if not manifest.is_file():
        parser.error(f"dataset manifest not found: {manifest}")
    payload = json.loads(manifest.read_text())
    print(json.dumps(payload["counts"], indent=2, sort_keys=True))
