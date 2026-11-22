from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ja_media_frontend.srt_cleaning.commands import run_generate, run_reconstruct
from ja_media_frontend.srt_cleaning.pipeline.provider_batch import run_provider_batch
from ja_media_frontend.srt_cleaning.review.command import run_review
from ja_media_frontend.srt_cleaning.settings import SrtCleanSettings
from ja_media_frontend.srt_cleaning.pipeline.smoke import (
    fetch_metadata,
    fetch_subtitle_inventory,
    run_smoke_test,
)
from ja_media_frontend.srt_cleaning.pipeline.vllm_batch import run_vllm_batch


HOUSE_STYLE_PATH = Path(__file__).parents[1] / "house-style.md"
BACKEND_CHOICES = ("vllm", "openai", "deepseek", "custom")


def build_parser(settings: SrtCleanSettings | None = None) -> argparse.ArgumentParser:
    cfg = settings or SrtCleanSettings.load()
    parser = argparse.ArgumentParser(
        description="SRT Cleaning Batch Pipeline - Generate, Run, Reconstruct, Review"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional TOML config file (defaults to ~/.config/ja-media-toolkit/config.toml)",
    )
    subparsers = parser.add_subparsers(dest="command")
    add_generate_parser(subparsers, cfg)
    add_run_parser(subparsers, cfg)
    add_reconstruct_parser(subparsers, cfg)
    add_review_parser(subparsers, cfg)
    return parser


def add_generate_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    cfg: SrtCleanSettings,
) -> None:
    gen = subparsers.add_parser(
        "generate",
        help="Generate SRT cleaning shards (or --dry-run to inspect inventory)",
    )
    gen.add_argument("--anilist", help="Comma-separated AniList IDs")
    gen.add_argument("--anilist-file", help="File with one AniList ID per line")
    gen.add_argument(
        "--source-manifest",
        help="Regenerate from the exact cached sources in a prior manifest",
    )
    gen.add_argument("--out", help="Explicit output prefix; omit to use workspace")
    gen.add_argument(
        "--workspace-root",
        default=None,
        help=f"Override workspace root (default: {cfg.workspace_root})",
    )
    gen.add_argument("--run-id", help="Workspace run ID; defaults to current")
    gen.add_argument(
        "--run-hash",
        action="store_true",
        help="Use a stable sha256-* run directory instead of clobbering current",
    )
    gen.add_argument("--model", default=cfg.generate.model, help="Chat model name")
    gen.add_argument("--window-size", type=int, default=cfg.generate.window_size)
    gen.add_argument("--context-cues", type=int, default=cfg.generate.context_cues)
    gen.add_argument("--group-prefix", action="append")
    gen.add_argument("--episode-one-only", action="store_true")
    gen.add_argument(
        "--flagged-windows-only",
        action="store_true",
        default=cfg.generate.flagged_windows_only,
    )
    gen.add_argument(
        "--max-requests-per-shard",
        type=int,
        default=cfg.generate.max_requests_per_shard,
    )
    gen.add_argument(
        "--max-bytes-per-shard",
        type=int,
        default=cfg.generate.max_bytes_per_shard,
    )
    gen.add_argument(
        "--single-jsonl",
        action="store_true",
        default=cfg.generate.single_jsonl,
    )
    gen.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch metadata and subtitle inventory without writing shards",
    )
    gen.add_argument(
        "--preview-srt",
        action="store_true",
        help="With --dry-run, preview the first matching SRT",
    )
    gen.add_argument(
        "--max-cues",
        type=int,
        default=5,
        help="With --dry-run --preview-srt, max cues to display",
    )


def add_run_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    cfg: SrtCleanSettings,
) -> None:
    run = subparsers.add_parser(
        "run",
        help="Execute generated shards against vLLM Docker or an OpenAI-compatible provider",
    )
    run.add_argument(
        "--backend",
        choices=BACKEND_CHOICES,
        default=cfg.default_backend,
        help=f"Execution backend (default: {cfg.default_backend})",
    )
    run.add_argument("--anilist", type=int, help="AniList ID for workspace autodetect")
    run.add_argument("--workspace-root", default=None, help="Override workspace root")
    run.add_argument("--run-id", default="current", help="Workspace run ID")
    run.add_argument("--input", help="Generated request JSONL")
    run.add_argument("--manifest", help="Window manifest JSONL (provider backends)")
    run.add_argument("--out", help="Output result JSONL")
    run.add_argument("--model", help="Override backend model")
    run.add_argument("--force", action="store_true", help="Replace an existing output")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved execution plan without running (vllm backend)",
    )
    # Provider-specific overrides
    run.add_argument("--base-url", default=cfg.provider.base_url)
    run.add_argument("--api-key-env", default=cfg.provider.api_key_env)
    run.add_argument(
        "--response-format",
        choices=("auto", "keep", "json-object", "none"),
        default=cfg.provider.response_format,
    )
    run.add_argument("--body-json", help="JSON object shallow-merged into request body")
    run.add_argument("--concurrency", type=int, default=cfg.provider.concurrency)
    run.add_argument("--limit", type=int, help="Run only the first N requests")
    run.add_argument("--timeout", type=float, default=cfg.provider.timeout)
    run.add_argument(
        "--request-attempts", type=int, default=cfg.provider.request_attempts
    )
    run.add_argument(
        "--repair-attempts",
        type=int,
        default=cfg.provider.repair_attempts,
        choices=(0, 1),
    )
    run.add_argument("--resume", action="store_true")
    # Local vLLM Docker overrides
    run.add_argument("--data-root", help="Host directory to mount as /data (vllm)")
    run.add_argument("--image", default=cfg.vllm.image)
    run.add_argument("--gpus", default=cfg.vllm.gpus)
    run.add_argument("--runtime", default=cfg.vllm.runtime)
    run.add_argument("--max-model-len", default=cfg.vllm.max_model_len)
    run.add_argument(
        "--max-num-batched-tokens", default=cfg.vllm.max_num_batched_tokens
    )
    run.add_argument("--hf-cache", default=cfg.vllm.hf_cache)
    run.add_argument("--vllm-cache", default="vllm-cache")
    run.add_argument("--env", action="append", default=[])
    run.add_argument("--extra-vllm-arg", action="append", default=[])
    run.add_argument("vllm_args", nargs=argparse.REMAINDER, default=[])


def add_reconstruct_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    cfg: SrtCleanSettings,
) -> None:
    reconstruct = subparsers.add_parser("reconstruct", help="Rebuild cleaned SRTs")
    reconstruct.add_argument("--anilist", type=int, help="AniList ID for workspace mode")
    reconstruct.add_argument("--workspace-root", default=None)
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


def add_review_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    cfg: SrtCleanSettings,
) -> None:
    review = subparsers.add_parser(
        "review",
        help="Review original vs cleaned SRTs with optional audio",
    )
    source = review.add_mutually_exclusive_group(required=True)
    source.add_argument("--anilist", type=int, help="AniList series ID")
    source.add_argument(
        "--run-dir",
        help="Reconstructed single- or multi-series run directory",
    )
    review.add_argument("--workspace-root", default=None)
    review.add_argument("--run-id", default="current", help="Workspace run ID")
    review.add_argument("--episode", type=int, help="Episode to open first (default: 1)")
    review.add_argument("--audio", help="Manual local audio/media file for playback")
    review.add_argument(
        "--audio-profile",
        default="portable-aac-v1",
        help="Derived audio profile to prefer (default: portable-aac-v1)",
    )


def _extract_config_path(argv: list[str] | None) -> Path | None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    known, _ = pre.parse_known_args(argv)
    return known.config


def main(argv: list[str] | None = None) -> None:
    config_path = _extract_config_path(argv)
    settings = SrtCleanSettings.load(config_path)
    parser = build_parser(settings)
    args = parser.parse_args(argv)

    if getattr(args, "workspace_root", None) is None and settings.workspace_root != Path(".ja-media-runs"):
        args.workspace_root = str(settings.workspace_root)

    if args.command == "generate":
        if args.dry_run:
            if not args.anilist or "," in str(args.anilist):
                parser.error("--dry-run requires a single integer --anilist ID")
            args.anilist = int(args.anilist)
            run_smoke_test(args)
        else:
            run_generate(
                args,
                house_style_path=HOUSE_STYLE_PATH,
                fetch_metadata=fetch_metadata,
                fetch_subtitle_inventory=fetch_subtitle_inventory,
            )
    elif args.command == "run":
        if args.backend == "vllm":
            if args.model is None:
                args.model = settings.vllm.model
            run_vllm_batch(args)
        else:
            args.provider = args.backend
            if args.model is None:
                args.model = settings.provider.model
            run_provider_batch(args)
    elif args.command == "reconstruct":
        run_reconstruct(args)
    elif args.command == "review":
        run_review(args)
    else:
        parser.print_help()
        sys.exit(1)
