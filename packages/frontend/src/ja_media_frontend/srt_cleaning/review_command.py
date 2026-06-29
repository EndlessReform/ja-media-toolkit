from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console

from ja_media_core.anilist_search import HttpAniListSearchClient
from ja_media_frontend.srt_cleaning.review_audio import load_review_audio
from ja_media_frontend.srt_cleaning.review_loader import load_review_workspace
from ja_media_frontend.srt_cleaning.review_tui import SrtCleaningReviewApp
from ja_media_frontend.srt_cleaning.workspace import run_for_anilist


console = Console()


def run_review(args: argparse.Namespace) -> None:
    """Resolve a workspace-backed cleaning run and launch the review TUI."""

    load_dotenv()
    workspace_root = Path(args.workspace_root).expanduser() if args.workspace_root else None
    run = run_for_anilist(args.anilist, workspace_root=workspace_root, run_id=args.run_id)
    if not run.manifest_path.exists():
        raise SystemExit(f"Missing review manifest: {run.manifest_path}")
    if not run.reconstruct_dir.exists():
        raise SystemExit(f"Missing reconstruct output: {run.reconstruct_dir}")

    workspace = load_review_workspace(run)
    if not workspace.sources:
        raise SystemExit(f"No reviewable source SRTs found in {run.run_dir}")

    episode = args.episode or 1
    manual_audio = Path(args.audio).expanduser().resolve() if args.audio else None
    initial_audio = load_review_audio(
        anilist_id=workspace.anilist_id,
        episode_number=episode,
        manual_audio=manual_audio,
        audio_profile=args.audio_profile,
    )
    app = SrtCleaningReviewApp(
        workspace=workspace,
        series_label=series_label(workspace.anilist_id),
        initial_episode=episode,
        audio_profile=args.audio_profile,
        manual_audio=manual_audio,
        initial_audio=initial_audio,
    )
    app.run()


def series_label(anilist_id: int) -> str:
    """Fetch a compact display label, falling back to the durable ID."""

    try:
        metadata = HttpAniListSearchClient().anime(
            anilist_id,
            fields=("title_english", "title_native", "title_romaji"),
        )
    except Exception as exc:
        console.print(f"[yellow]AniList metadata unavailable:[/] {exc}")
        return f"AniList {anilist_id}"
    for field in ("title_english", "title_romaji", "title_native"):
        value: Any = metadata.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"AniList {anilist_id}"
