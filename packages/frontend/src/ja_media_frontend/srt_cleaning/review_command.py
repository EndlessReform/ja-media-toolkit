from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
import json

from dotenv import load_dotenv
from rich.console import Console

from ja_media_core.anilist_search import HttpAniListSearchClient
from ja_media_frontend.srt_cleaning.review_audio import load_review_audio
from ja_media_frontend.srt_cleaning.review_loader import (
    load_review_directory,
    load_review_workspace,
)
from ja_media_frontend.srt_cleaning.review_tui import SrtCleaningReviewApp
from ja_media_frontend.srt_cleaning.workspace import run_for_anilist


console = Console()


def run_review(args: argparse.Namespace) -> None:
    """Resolve a workspace-backed cleaning run and launch the review TUI."""

    load_dotenv()
    alignment_case = (
        Path(args.alignment_case).expanduser().resolve()
        if args.alignment_case
        else None
    )
    if args.run_dir:
        workspace = load_review_directory(
            Path(args.run_dir), alignment_case=alignment_case
        )
    else:
        workspace_root = (
            Path(args.workspace_root).expanduser() if args.workspace_root else None
        )
        run = run_for_anilist(
            args.anilist,
            workspace_root=workspace_root,
            run_id=args.run_id,
        )
        if not run.manifest_path.exists():
            raise SystemExit(f"Missing review manifest: {run.manifest_path}")
        if not run.reconstruct_dir.exists():
            raise SystemExit(f"Missing reconstruct output: {run.reconstruct_dir}")
        workspace = load_review_workspace(run, alignment_case=alignment_case)
    if not workspace.sources:
        raise SystemExit(f"No reviewable source SRTs found in {workspace.run_dir}")

    first_key = workspace.episode_keys[0]
    initial_anilist_id = args.anilist or first_key[0]
    episode = args.episode or first_key[1]
    manual_audio = Path(args.audio).expanduser().resolve() if args.audio else None
    alignment_payload = (
        json.loads(alignment_case.read_text(encoding="utf-8"))
        if alignment_case is not None
        else None
    )
    single_alignment_case = bool(
        alignment_payload
        and alignment_payload.get("schema_name") == "ja-media.forced-alignment.case"
    )
    initial_sources = workspace.sources_for_episode(initial_anilist_id, episode)
    initial_source_index = workspace.preferred_source_index(
        initial_anilist_id, episode
    )
    prepared_audio = (
        initial_sources[initial_source_index].alignment_audio_path
        if initial_sources
        else None
    )
    selected_audio = manual_audio or prepared_audio
    initial_audio = load_review_audio(
        anilist_id=initial_anilist_id,
        episode_number=episode,
        manual_audio=selected_audio,
        audio_profile=args.audio_profile,
        manual_audio_status=(
            "using manually assigned audio"
            if manual_audio is not None
            else "using prepared alignment audio"
        ),
    )
    app = SrtCleaningReviewApp(
        workspace=workspace,
        series_label=series_label(initial_anilist_id),
        initial_anilist_id=initial_anilist_id,
        initial_episode=episode,
        audio_profile=args.audio_profile,
        manual_audio=manual_audio,
        initial_audio=initial_audio,
        alignment_eval_path=(
            alignment_case.parent / "stability" / "results.json"
            if alignment_case is not None and single_alignment_case
            else None
        ),
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
