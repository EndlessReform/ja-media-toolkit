"""CLI adapter for the interactive derived anime audio workflow."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from ja_media_core.anilist_search import HttpAniListSearchClient
from ja_media_frontend.audio_library.prompts import ConsoleWizardPrompts
from ja_media_frontend.audio_library.wizard import (
    IngestWizardRequest,
    build_ingest_plan,
    execute_ingest_plan,
)


def register_audio_library_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``ja-media audio-library`` command group."""

    parser = subparsers.add_parser(
        "audio-library",
        help="Build a durable Audiobookshelf-compatible anime audio library",
    )
    commands = parser.add_subparsers(dest="audio_library_command")
    ingest = commands.add_parser(
        "ingest",
        help="Interactively map and materialize one anime source directory",
    )
    ingest.add_argument("--source", type=Path, required=True)
    ingest.add_argument("--destination", type=Path, required=True)
    ingest.add_argument("--anilist", type=int)
    ingest.add_argument(
        "--profile",
        choices=("portable-aac-v1",),
        default="portable-aac-v1",
    )
    ingest.add_argument(
        "--audio-stream",
        type=int,
        help="Zero-based audio-stream ordinal to use for every source.",
    )
    ingest.add_argument(
        "--language",
        action="append",
        help="Preferred stream language tag. Repeat to define fallback order.",
    )
    ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and confirm the complete plan without writing files.",
    )
    ingest.add_argument(
        "--resume",
        action="store_true",
        help="Skip verified artifacts with matching source fingerprints.",
    )
    ingest.add_argument(
        "--replace",
        action="store_true",
        help="Replace conflicting artifacts after the final plan confirmation.",
    )
    ingest.set_defaults(audio_library_parser=parser)


def run_audio_library_ingest(args: argparse.Namespace) -> None:
    """Run the interactive plan-first ingest command."""

    load_dotenv()
    console = Console()
    prompts = ConsoleWizardPrompts(console)
    request = IngestWizardRequest(
        source=args.source.expanduser().resolve(),
        destination=args.destination.expanduser().resolve(),
        client=HttpAniListSearchClient(),
        prompts=prompts,
        anilist_id=args.anilist,
        audio_stream_ordinal=args.audio_stream,
        preferred_languages=tuple(args.language or ("jpn", "ja")),
    )
    try:
        plan = build_ingest_plan(request)
        if plan is None:
            console.print("[yellow]Cancelled before materialization; nothing was written.[/yellow]")
            return
        if args.dry_run:
            console.print("[green]Dry run complete; nothing was written.[/green]")
            return
        summary = execute_ingest_plan(
            plan,
            resume=args.resume,
            replace_existing=args.replace,
            notice=prompts.notice,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        return
    except Exception as error:
        raise SystemExit(f"audio-library ingest failed: {error}") from error

    console.print(
        f"[green]Created {len(summary.created)}[/green], "
        f"[cyan]skipped {len(summary.skipped)}[/cyan], "
        f"[red]failed {len(summary.failed)}[/red]."
    )
    if summary.failed:
        console.print("Rerun with --resume after correcting failures.")
