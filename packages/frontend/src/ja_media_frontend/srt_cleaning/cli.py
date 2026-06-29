from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ja_media_frontend.srt_cleaning.commands import run_generate, run_reconstruct
from ja_media_frontend.srt_cleaning.smoke import (
    fetch_metadata,
    fetch_subtitle_inventory,
    run_smoke_test,
)
from ja_media_frontend.srt_cleaning.vllm_batch import (
    register_run_vllm_parser,
    run_vllm_batch,
)


HOUSE_STYLE_PATH = Path(__file__).parents[1] / "house-style.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SRT Cleaning Batch Pipeline - Generator & Reconciler"
    )
    subparsers = parser.add_subparsers(dest="command")
    add_smoke_parser(subparsers)
    add_generate_parser(subparsers)
    register_run_vllm_parser(subparsers)
    add_reconstruct_parser(subparsers)
    return parser


def add_smoke_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    smoke = subparsers.add_parser("smoke-test", help="Fetch metadata & inventory")
    smoke.add_argument("--anilist", type=int, required=True, help="AniList series ID")
    smoke.add_argument(
        "--episode-one-only",
        action="store_true",
        help="Filter subtitles to episode 1 only",
    )
    smoke.add_argument(
        "--group-prefix",
        action="append",
        help="Filter subtitles by repo_path or filename prefix; repeatable",
    )
    smoke.add_argument(
        "--preview-srt",
        action="store_true",
        help="Download and show a preview of the first matching SRT",
    )
    smoke.add_argument(
        "--max-cues",
        type=int,
        default=5,
        help="Max cues to show in SRT preview",
    )


def add_generate_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    generate = subparsers.add_parser("generate", help="Generate SRT cleaning shards")
    generate.add_argument("--anilist", help="Comma-separated AniList IDs")
    generate.add_argument("--anilist-file", help="File with one AniList ID per line")
    generate.add_argument(
        "--out",
        help="Explicit output prefix; omit to use the AniList workspace",
    )
    generate.add_argument("--workspace-root", help="Override .ja-media-runs root")
    generate.add_argument("--run-id", help="Workspace run ID; defaults to current")
    generate.add_argument(
        "--run-hash",
        action="store_true",
        help="Use a stable sha256-* run directory instead of clobbering current",
    )
    generate.add_argument("--model", default="gpt-5.5", help="Chat model name")
    generate.add_argument("--window-size", type=int, default=10)
    generate.add_argument(
        "--context-cues",
        type=int,
        default=0,
        help="Opt-in surrounding cue context per side",
    )
    generate.add_argument("--group-prefix", action="append")
    generate.add_argument("--episode-one-only", action="store_true")
    generate.add_argument("--max-requests-per-shard", type=int, default=50_000)
    generate.add_argument("--max-bytes-per-shard", type=int, default=200 * 1000 * 1000)
    generate.add_argument(
        "--single-jsonl",
        action="store_true",
        help="Write all requests to a single batch file",
    )


def add_reconstruct_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    reconstruct = subparsers.add_parser("reconstruct", help="Rebuild cleaned SRTs")
    reconstruct.add_argument("--anilist", type=int, help="AniList ID for workspace mode")
    reconstruct.add_argument("--workspace-root", help="Override .ja-media-runs root")
    reconstruct.add_argument("--run-id", default="current", help="Workspace run ID")
    reconstruct.add_argument("--manifest", help="Explicit generator manifest JSONL")
    reconstruct.add_argument(
        "--batch-output",
        action="append",
        help="OpenAI-style batch output JSONL; repeat for multiple files",
    )
    reconstruct.add_argument("--out-dir", help="Directory for reconstruction output")
    reconstruct.add_argument("--allow-partial", action="store_true")
    reconstruct.add_argument("--no-archive", action="store_true")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "smoke-test":
        run_smoke_test(args)
    elif args.command == "generate":
        run_generate(
            args,
            house_style_path=HOUSE_STYLE_PATH,
            fetch_metadata=fetch_metadata,
            fetch_subtitle_inventory=fetch_subtitle_inventory,
        )
    elif args.command == "run-vllm":
        run_vllm_batch(args)
    elif args.command == "reconstruct":
        run_reconstruct(args)
    else:
        parser.print_help()
        sys.exit(1)
