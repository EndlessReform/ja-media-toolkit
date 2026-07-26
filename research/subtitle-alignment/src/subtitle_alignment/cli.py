"""Private command line for the subtitle-alignment experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from subtitle_alignment.access import REPO_ROOT
from subtitle_alignment.identity import run_identity_survey
from subtitle_alignment.identity_report import write_identity_report
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

    identity = commands.add_parser(
        "identity", help="run and report the naive Gate 1 identity baseline"
    )
    identity.add_argument("dataset", type=Path)
    identity.add_argument("--workers", type=int, default=8)
    identity.add_argument("--output-root", type=Path, default=Path("output"))

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

    dataset = args.dataset.expanduser().resolve()
    manifest = dataset / "manifest.json"
    if not manifest.is_file():
        parser.error(f"dataset manifest not found: {manifest}")
    if args.command == "inspect":
        payload = json.loads(manifest.read_text())
        print(json.dumps(payload["counts"], indent=2, sort_keys=True))
        return
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")
    result = run_identity_survey(
        dataset,
        output_root=args.output_root.expanduser().resolve(),
        workers=args.workers,
    )
    pdf = write_identity_report(
        result,
        pdf_root=REPO_ROOT / "output" / "pdf",
    )
    print(f"results={result}")
    print(f"pdf={pdf}")
