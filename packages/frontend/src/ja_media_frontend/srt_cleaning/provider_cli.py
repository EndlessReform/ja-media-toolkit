from __future__ import annotations

import argparse


def register_run_provider_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "run-provider",
        help="Run cleaning JSONL through an OpenAI-compatible provider",
    )
    parser.add_argument(
        "--anilist", type=int, help="AniList ID for workspace autodetect"
    )
    parser.add_argument("--workspace-root", help="Override .ja-media-runs root")
    parser.add_argument("--run-id", default="current", help="Workspace run ID")
    parser.add_argument("--input", help="Generated request JSONL")
    parser.add_argument("--manifest", help="Window manifest JSONL")
    parser.add_argument("--out", help="Provider result JSONL")
    parser.add_argument(
        "--provider",
        choices=("openai", "deepseek", "custom"),
        required=True,
    )
    parser.add_argument("--model", help="Override the provider preset model")
    parser.add_argument("--base-url", help="Override the OpenAI-compatible API base")
    parser.add_argument(
        "--api-key-env", help="Environment variable containing the API key"
    )
    parser.add_argument(
        "--response-format",
        choices=("auto", "keep", "json-object", "none"),
        default="auto",
    )
    parser.add_argument(
        "--body-json",
        help="JSON object shallow-merged into every request body",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, help="Run only the first N requests")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--request-attempts",
        type=int,
        default=8,
        help="Maximum attempts for 429, transient HTTP, and network failures",
    )
    parser.add_argument("--repair-attempts", type=int, default=1, choices=(0, 1))
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append to an interrupted run and skip IDs already in the output",
    )
    parser.add_argument(
        "--force", action="store_true", help="Replace an existing output"
    )
