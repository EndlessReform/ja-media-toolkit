"""SRT Cleaning Batch Pipeline — Generator & Reconciler

Generates sharded OpenAI-compatible batch JSONL from Kitsunekko SRT candidates,
then reconstructs cleaned subtitle artifacts from batch output.

Subcommands:
    smoke-test   Fetch metadata & subtitle inventory for an AniList ID.
    generate     (TODO) Generate sharded batch JSONL from SRT candidates.
    reconstruct  (TODO) Reconstruct cleaned SRTs from batch output.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ja_media_core import (
    HttpAniListSearchClient,
    HttpKitsunekkoSubtitlesClient,
    load_config,
    media_filename,
    parse_srt,
)
from ja_media_frontend.srt_cleaning.commands import run_generate, run_reconstruct

console = Console()

# ---------------------------------------------------------------------------
# Metadata context
# ---------------------------------------------------------------------------

ANIME_FIELDS = (
    "title_english",
    "title_native",
    "title_romaji",
    "description",
    "characters",
)

HOUSE_STYLE_PATH = Path(__file__).parent / "house-style.md"


@dataclass
class MetadataContext:
    """Resolved series metadata for prompt context."""

    anilist_id: int
    title_english: str | None
    title_native: str | None
    title_romaji: str | None
    description: str | None
    characters: list[dict[str, Any]]
    metadata_warnings: list[str]


def fetch_metadata(anilist_id: int) -> MetadataContext:
    """Fetch AniList metadata via the LAN search service."""
    client = HttpAniListSearchClient()
    metadata = client.anime(anilist_id, fields=ANIME_FIELDS)
    warnings: list[str] = []

    chars = metadata.get("characters")
    if chars is None:
        warnings.append("characters field missing from metadata response")
        chars = []
    elif not isinstance(chars, list):
        warnings.append(f"characters field is {type(chars).__name__}, expected list")
        chars = []

    description = metadata.get("description")
    if description is not None and isinstance(description, str):
        from html.parser import HTMLParser

        class Stripper(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.parts: list[str] = []

            def handle_data(self, data: str) -> None:
                self.parts.append(data)

        stripper = Stripper()
        stripper.feed(description)
        description = " ".join(stripper.parts).strip()

    return MetadataContext(
        anilist_id=anilist_id,
        title_english=metadata.get("title_english"),
        title_native=metadata.get("title_native"),
        title_romaji=metadata.get("title_romaji"),
        description=description,
        characters=chars,
        metadata_warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Subtitle inventory
# ---------------------------------------------------------------------------

@dataclass
class SubtitleInventoryEntry:
    """One Kitsunekko subtitle file with parsed metadata."""

    subtitle_id: str
    repo_path: str
    name: str
    episode: int | None
    is_srt: bool


@dataclass
class SubtitleInventory:
    """Subtitle files for a series, filtered and annotated."""

    anilist_id: int
    entries: list[SubtitleInventoryEntry]
    total_files: int
    srt_count: int
    non_srt_count: int


def fetch_subtitle_inventory(
    anilist_id: int,
    *,
    episode_one_only: bool = False,
    group_prefixes: tuple[str, ...] = (),
) -> SubtitleInventory:
    """Fetch subtitle file list from Kitsunekko service."""
    client = HttpKitsunekkoSubtitlesClient()
    resp = client.anilist_files(anilist_id)

    entries: list[SubtitleInventoryEntry] = []
    for f in resp.files:
        filename = f.get("filename") or f.get("name", "")
        repo_path = f.get("repo_path", "")
        subtitle_id = f.get("subtitle_id", "")
        if not isinstance(subtitle_id, str):
            subtitle_id = str(subtitle_id)
        is_srt = filename.lower().endswith(".srt") or repo_path.lower().endswith(".srt")

        stem = Path(filename).stem
        episode = media_filename.suggest_ordinary_episode(stem)

        if group_prefixes:
            prefix_match = any(repo_path.startswith(p) or filename.startswith(p) for p in group_prefixes)
            if not prefix_match:
                continue

        if episode_one_only and episode != 1:
            continue

        entries.append(
            SubtitleInventoryEntry(
                subtitle_id=subtitle_id,
                repo_path=repo_path,
                name=filename or Path(repo_path).name,
                episode=episode,
                is_srt=is_srt,
            )
        )

    srt_count = sum(1 for e in entries if e.is_srt)
    return SubtitleInventory(
        anilist_id=anilist_id,
        entries=entries,
        total_files=resp.count,
        srt_count=srt_count,
        non_srt_count=len(entries) - srt_count,
    )


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def display_metadata(ctx: MetadataContext) -> None:
    """Render metadata context as a Rich panel."""
    lines: list[Text] = []

    def _t(label: str, value: str | None) -> Text:
        return Text.assemble((f"{label}: ", "bold"), (value or "—", ""))

    for label, key in [
        ("AL", ctx.title_english),
        ("JA", ctx.title_native),
        ("Romaji", ctx.title_romaji),
    ]:
        lines.append(_t(label, key))

    if ctx.description:
        desc_lines = [Text("Description:", style="bold")]
        for i in range(0, len(ctx.description), 100):
            chunk = ctx.description[i : i + 100]
            desc_lines.append(Text(chunk))
        lines.extend(desc_lines)

    if ctx.characters:
        lines.append(Text(f"Characters: {len(ctx.characters)}", style="bold"))

    if ctx.metadata_warnings:
        for w in ctx.metadata_warnings:
            lines.append(Text(f"  ⚠ {w}", style="yellow"))

    panel = Panel(
        Text("\n").join(lines),
        title=f"[cyan]AniList #{ctx.anilist_id}[/]",
        border_style="blue",
    )
    console.print(panel)


def display_inventory(inv: SubtitleInventory) -> None:
    """Render subtitle inventory as a Rich table."""
    console.print(f"\n[bold]Subtitles:[/] {inv.total_files} total, "
                  f"{inv.srt_count} SRT, {inv.non_srt_count} other")

    if not inv.entries:
        if inv.total_files == 0:
            console.print("[yellow]No subtitles found for this series.[/]")
        else:
            console.print("[yellow]No entries matched the current filters.[/]")
        return

    table = Table(title="Subtitle Inventory")
    table.add_column("Episode", justify="right", style="cyan", width=8)
    table.add_column("SRT", justify="center", width=4)
    table.add_column("File Name", style="green")
    table.add_column("Repo Path", style="dim")

    for entry in inv.entries:
        episode_str = str(entry.episode) if entry.episode is not None else "?"
        srt_mark = "✓" if entry.is_srt else "✗"
        srt_style = "green" if entry.is_srt else "red"
        display_name = entry.name or Path(entry.repo_path).name
        table.add_row(
            episode_str,
            f"[{srt_style}]{srt_mark}[/]",
            display_name,
            entry.repo_path,
        )

    console.print(table)


def display_srt_preview(subtitle_id: str, name: str, max_cues: int = 5) -> None:
    """Download and show a preview of an SRT file."""
    client = HttpKitsunekkoSubtitlesClient()
    content_bytes = client.file_content(subtitle_id)
    content = content_bytes.decode("utf-8-sig")

    try:
        cues = parse_srt(content)
    except Exception as e:
        console.print(f"[red]Failed to parse SRT: {e}[/]")
        return

    console.print(f"\n[bold]SRT Preview: {name}[/] ([dim]{len(cues)} cues[/])")
    table = Table()
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Start", style="cyan", width=10)
    table.add_column("End", style="cyan", width=10)
    table.add_column("Text", style="white")

    for cue in cues[:max_cues]:
        start_ts = f"{cue.start_s:,.3f}"
        end_ts = f"{cue.end_s:,.3f}"
        table.add_row(str(cue.index), start_ts, end_ts, cue.text[:80])

    console.print(table)

    total_chars = sum(len(cue.text) for cue in cues)
    kana = sum(1 for cue in cues for ch in cue.text if "\u3040" <= ch <= "\u30ff")
    console.print(f"[dim]{total_chars} total chars, {kana} kana[/]")


def display_character_sample(characters: list[dict[str, Any]], max_show: int = 20) -> None:
    """Show a character table from the metadata."""
    if not characters:
        return

    table = Table(title="Character Sample")
    table.add_column("Name", style="green", width=25)
    table.add_column("Native", style="magenta", width=25)
    table.add_column("Role", style="dim", width=15)

    for char in characters[:max_show]:
        node = char.get("node", char)
        name_info = node.get("name", {}) if isinstance(node, dict) else {}
        name_full = name_info.get("full", "") if isinstance(name_info, dict) else ""
        name_native = name_info.get("native", "") if isinstance(name_info, dict) else ""
        role = char.get("role", "")
        table.add_row(str(name_full), str(name_native), str(role))

    console.print(table)


# ---------------------------------------------------------------------------
# Smoke test subcommand
# ---------------------------------------------------------------------------

def run_smoke_test(args: argparse.Namespace) -> None:
    """Fetch metadata and subtitle inventory for one AniList ID."""
    anilist_id = args.anilist

    console.print("[bold]Config...[/]", end=" ")
    cfg = load_config()
    console.print(f"[green]loaded[/] [dim](root_url: {cfg.services.root_url})[/]")

    console.print(f"[bold]Fetching metadata for AniList #{anilist_id}...[/]")
    ctx = fetch_metadata(anilist_id)
    display_metadata(ctx)
    display_character_sample(ctx.characters)

    console.print(f"\n[bold]Fetching subtitle inventory...[/]")
    inv = fetch_subtitle_inventory(
        anilist_id,
        episode_one_only=args.episode_one_only,
        group_prefixes=tuple(args.group_prefix) if args.group_prefix else (),
    )
    display_inventory(inv)

    if args.preview_srt and inv.entries:
        srt_entries = [e for e in inv.entries if e.is_srt]
        target = srt_entries[0] if srt_entries else inv.entries[0]
        console.print(f"\n[bold]Downloading SRT preview: {target.name}...[/]")
        display_srt_preview(target.subtitle_id, target.name, max_cues=args.max_cues)
    elif not inv.entries:
        console.print("[yellow]No entries to preview.[/]")

    console.print("\n[green bold]Smoke test complete.[/]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SRT Cleaning Batch Pipeline — Generator & Reconciler"
    )
    subparsers = parser.add_subparsers(dest="command")

    smoke = subparsers.add_parser("smoke-test", help="Fetch metadata & inventory for an AniList ID")
    smoke.add_argument("--anilist", type=int, required=True, help="AniList series ID")
    smoke.add_argument(
        "--episode-one-only", action="store_true",
        help="Filter subtitles to episode 1 only",
    )
    smoke.add_argument(
        "--group-prefix", action="append",
        help="Filter subtitles by repo_path or filename prefix (repeatable)",
    )
    smoke.add_argument(
        "--preview-srt", action="store_true",
        help="Download and show a preview of the first matching SRT",
    )
    smoke.add_argument(
        "--max-cues", type=int, default=5,
        help="Max cues to show in SRT preview (default: 5)",
    )
    generate = subparsers.add_parser("generate", help="Generate SRT cleaning batch shards")
    generate.add_argument("--anilist", help="Comma-separated AniList IDs")
    generate.add_argument("--anilist-file", help="File with one AniList ID per line")
    generate.add_argument("--out", required=True, help="Output prefix for batch artifacts")
    generate.add_argument("--model", default="gpt-5.5", help="Chat model name")
    generate.add_argument("--window-cues", type=int, default=10)
    generate.add_argument("--context-cues", type=int, default=2)
    generate.add_argument("--group-prefix", action="append")
    generate.add_argument("--episode-one-only", action="store_true")
    generate.add_argument("--max-requests-per-shard", type=int, default=50_000)
    generate.add_argument("--max-bytes-per-shard", type=int, default=200 * 1000 * 1000)

    reconstruct = subparsers.add_parser("reconstruct", help="Rebuild cleaned SRTs")
    reconstruct.add_argument("--manifest", required=True, help="Generator manifest JSONL")
    reconstruct.add_argument(
        "--batch-output",
        required=True,
        action="append",
        help="OpenAI-style batch output JSONL; repeat for multiple files",
    )
    reconstruct.add_argument("--out-dir", required=True, help="Directory for reconstruction output")
    reconstruct.add_argument("--allow-partial", action="store_true")
    reconstruct.add_argument("--no-archive", action="store_true")

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
    elif args.command == "reconstruct":
        run_reconstruct(args)
    else:
        parser.print_help()
        sys.exit(1)
