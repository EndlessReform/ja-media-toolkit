from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

from ja_media_core import HttpKitsunekkoSubtitlesClient
from ja_media_frontend.srt_cleaning.batch import (
    build_batch_row,
    build_manifest_row,
    build_windows,
    prefix_artifact_path,
    sha256_text,
    write_generation_artifacts,
    write_shards_summary,
)
from ja_media_frontend.srt_cleaning.contracts import SourceDocument
from ja_media_frontend.srt_cleaning.reconstruct import reconstruct_from_batch


console = Console()


def parse_anilist_ids(args: argparse.Namespace) -> list[int]:
    """Parse AniList IDs from comma-separated CLI input and optional file."""
    ids: list[int] = []
    if args.anilist:
        ids.extend(int(part.strip()) for part in args.anilist.split(",") if part.strip())
    if args.anilist_file:
        for line in Path(args.anilist_file).read_text(encoding="utf-8").splitlines():
            clean = line.split("#", 1)[0].strip()
            if clean:
                ids.append(int(clean))
    return list(dict.fromkeys(ids))


def render_series_context(ctx: Any) -> str:
    """Render compact metadata context for a cleaning window prompt."""
    lines = [
        f"AniList ID: {ctx.anilist_id}",
        f"English title: {ctx.title_english or 'unknown'}",
        f"Native title: {ctx.title_native or 'unknown'}",
        f"Romaji title: {ctx.title_romaji or 'unknown'}",
    ]
    if ctx.description:
        lines.append(f"Synopsis: {ctx.description}")
    names = [name for char in ctx.characters[:30] if (name := format_character_name(char))]
    if names:
        lines.append("Characters: " + ", ".join(names))
    return "\n".join(lines)


def format_character_name(char: dict[str, Any]) -> str | None:
    """Format AniList character names with native names first for JP biasing."""
    node = char.get("node", char)
    name_info = node.get("name", {}) if isinstance(node, dict) else {}
    if not isinstance(name_info, dict):
        return None

    native = clean_optional_text(name_info.get("native"))
    full = clean_optional_text(name_info.get("full"))
    alternatives: list[str] = []
    raw_alternatives = name_info.get("alternative", ())
    if isinstance(raw_alternatives, list):
        for item in raw_alternatives:
            if alternative := clean_optional_text(item):
                alternatives.append(alternative)
    romanized = [value for value in [full, *alternatives] if value and value != native]
    if native and romanized:
        return f"{native} ({' / '.join(romanized[:2])})"
    return native or full


def clean_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def run_generate(
    args: argparse.Namespace,
    *,
    house_style_path: Path,
    fetch_metadata: Any,
    fetch_subtitle_inventory: Any,
) -> None:
    """Generate batch shards, manifest rows, and cached source SRTs."""
    anilist_ids = parse_anilist_ids(args)
    if not anilist_ids:
        console.print("[bold red]Error:[/] Provide --anilist or --anilist-file.")
        sys.exit(2)

    policy_text = house_style_path.read_text(encoding="utf-8")
    policy_sha = sha256_text(policy_text)
    output_prefix = Path(args.out).expanduser().resolve()
    sources_dir = output_prefix.parent / f"{output_prefix.name}.sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    subtitle_client = HttpKitsunekkoSubtitlesClient()

    for anilist_id in anilist_ids:
        ctx = fetch_metadata(anilist_id)
        inv = fetch_subtitle_inventory(
            anilist_id,
            episode_one_only=args.episode_one_only,
            group_prefixes=tuple(args.group_prefix or ()),
        )
        series_context = render_series_context(ctx)
        for entry in [item for item in inv.entries if item.is_srt]:
            source_text = subtitle_client.file_content(entry.subtitle_id).decode("utf-8-sig")
            source_sha = sha256_text(source_text)
            source_path = sources_dir / f"{entry.subtitle_id}.{source_sha[:12]}.srt"
            source_path.write_text(source_text, encoding="utf-8")
            source = SourceDocument(
                anilist_id=anilist_id,
                subtitle_id=entry.subtitle_id,
                repo_path=entry.repo_path,
                filename=entry.name,
                source_path=source_path,
                metadata_warnings=tuple(ctx.metadata_warnings),
            )
            windows = build_windows(
                source,
                source_text,
                window_cues=args.window_cues,
                context_cues=args.context_cues,
                prompt_policy_sha256=policy_sha,
            )
            rows.extend(
                build_batch_row(
                    window,
                    model=args.model,
                    policy_text=policy_text,
                    series_context=series_context,
                )
                for window in windows
            )
            manifest_rows.extend(
                build_manifest_row(window, model=args.model) for window in windows
            )

    shards = write_generation_artifacts(
        rows,
        manifest_rows,
        output_prefix=output_prefix,
        max_requests_per_shard=args.max_requests_per_shard,
        max_bytes_per_shard=args.max_bytes_per_shard,
    )
    write_shards_summary(prefix_artifact_path(output_prefix, ".shards.json"), shards, model=args.model)
    console.print(
        f"[green]Generated[/] {len(rows)} requests across {len(shards)} shard(s); "
        f"manifest: [cyan]{prefix_artifact_path(output_prefix, '.manifest.jsonl')}[/]"
    )


def run_reconstruct(args: argparse.Namespace) -> None:
    """Reconstruct cleaned SRTs from one or more batch output files."""
    summary = reconstruct_from_batch(
        batch_output_paths=[Path(path).expanduser().resolve() for path in args.batch_output],
        manifest_path=Path(args.manifest).expanduser().resolve(),
        output_dir=Path(args.out_dir).expanduser().resolve(),
        allow_partial=args.allow_partial,
        archive=not args.no_archive,
    )
    console.print(
        f"[green]Reconstructed[/] {summary.cleaned_srts} cleaned SRT(s), "
        f"{summary.errors} error(s), {summary.dlq} DLQ row(s)."
    )
    console.print(f"Decisions: [cyan]{summary.decisions_path}[/]")
    console.print(f"Errors: [cyan]{summary.errors_path}[/]")
    console.print(f"DLQ: [cyan]{summary.dlq_path}[/]")
    if summary.archive_path:
        console.print(f"Archive: [cyan]{summary.archive_path}[/]")
