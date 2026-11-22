from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
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


console = Console()

ANIME_FIELDS = (
    "title_english",
    "title_native",
    "title_romaji",
    "description",
    "characters",
)


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


class DescriptionStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def fetch_metadata(anilist_id: int) -> MetadataContext:
    """Fetch AniList metadata via the LAN search service."""

    metadata = HttpAniListSearchClient().anime(anilist_id, fields=ANIME_FIELDS)
    warnings: list[str] = []

    chars = metadata.get("characters")
    if chars is None:
        warnings.append("characters field missing from metadata response")
        chars = []
    elif not isinstance(chars, list):
        warnings.append(f"characters field is {type(chars).__name__}, expected list")
        chars = []

    description = metadata.get("description")
    if isinstance(description, str):
        stripper = DescriptionStripper()
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


def fetch_subtitle_inventory(
    anilist_id: int,
    *,
    episode_one_only: bool = False,
    group_prefixes: tuple[str, ...] = (),
) -> SubtitleInventory:
    """Fetch subtitle file list from Kitsunekko service."""

    resp = HttpKitsunekkoSubtitlesClient().anilist_files(anilist_id)
    entries: list[SubtitleInventoryEntry] = []
    for item in resp.files:
        filename = item.get("filename") or item.get("name", "")
        repo_path = item.get("repo_path", "")
        subtitle_id = item.get("subtitle_id", "")
        if not isinstance(subtitle_id, str):
            subtitle_id = str(subtitle_id)
        is_srt = filename.lower().endswith(".srt") or repo_path.lower().endswith(".srt")
        episode = media_filename.suggest_ordinary_episode(Path(filename).stem)

        if group_prefixes:
            prefix_match = any(
                repo_path.startswith(prefix) or filename.startswith(prefix)
                for prefix in group_prefixes
            )
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

    srt_count = sum(1 for entry in entries if entry.is_srt)
    return SubtitleInventory(
        anilist_id=anilist_id,
        entries=entries,
        total_files=resp.count,
        srt_count=srt_count,
        non_srt_count=len(entries) - srt_count,
    )


def run_smoke_test(args: argparse.Namespace) -> None:
    """Fetch metadata and subtitle inventory for one AniList ID."""

    console.print("[bold]Config...[/]", end=" ")
    cfg = load_config()
    console.print(f"[green]loaded[/] [dim](root_url: {cfg.services.root_url})[/]")

    console.print(f"[bold]Fetching metadata for AniList #{args.anilist}...[/]")
    ctx = fetch_metadata(args.anilist)
    display_metadata(ctx)
    display_character_sample(ctx.characters)

    console.print("\n[bold]Fetching subtitle inventory...[/]")
    inv = fetch_subtitle_inventory(
        args.anilist,
        episode_one_only=args.episode_one_only,
        group_prefixes=tuple(args.group_prefix or ()),
    )
    display_inventory(inv)

    if args.preview_srt and inv.entries:
        srt_entries = [entry for entry in inv.entries if entry.is_srt]
        target = srt_entries[0] if srt_entries else inv.entries[0]
        console.print(f"\n[bold]Downloading SRT preview: {target.name}...[/]")
        display_srt_preview(target.subtitle_id, target.name, max_cues=args.max_cues)
    elif not inv.entries:
        console.print("[yellow]No entries to preview.[/]")

    console.print("\n[green bold]Smoke test complete.[/]")


def display_metadata(ctx: MetadataContext) -> None:
    """Render metadata context as a Rich panel."""

    lines: list[Text] = []
    for label, value in [
        ("AL", ctx.title_english),
        ("JA", ctx.title_native),
        ("Romaji", ctx.title_romaji),
    ]:
        lines.append(Text.assemble((f"{label}: ", "bold"), (value or "-", "")))

    if ctx.description:
        lines.append(Text("Description:", style="bold"))
        lines.extend(Text(ctx.description[index : index + 100]) for index in range(0, len(ctx.description), 100))
    if ctx.characters:
        lines.append(Text(f"Characters: {len(ctx.characters)}", style="bold"))
    lines.extend(Text(f"  ! {warning}", style="yellow") for warning in ctx.metadata_warnings)

    console.print(
        Panel(
            Text("\n").join(lines),
            title=f"[cyan]AniList #{ctx.anilist_id}[/]",
            border_style="blue",
        )
    )


def display_inventory(inv: SubtitleInventory) -> None:
    """Render subtitle inventory as a Rich table."""

    console.print(
        f"\n[bold]Subtitles:[/] {inv.total_files} total, "
        f"{inv.srt_count} SRT, {inv.non_srt_count} other"
    )
    if not inv.entries:
        message = "No subtitles found for this series." if inv.total_files == 0 else "No entries matched the current filters."
        console.print(f"[yellow]{message}[/]")
        return

    table = Table(title="Subtitle Inventory")
    table.add_column("Episode", justify="right", style="cyan", width=8)
    table.add_column("SRT", justify="center", width=4)
    table.add_column("File Name", style="green")
    table.add_column("Repo Path", style="dim")

    for entry in inv.entries:
        episode = str(entry.episode) if entry.episode is not None else "?"
        srt_mark = "Y" if entry.is_srt else "N"
        srt_style = "green" if entry.is_srt else "red"
        table.add_row(episode, f"[{srt_style}]{srt_mark}[/]", entry.name, entry.repo_path)

    console.print(table)


def display_srt_preview(subtitle_id: str, name: str, max_cues: int = 5) -> None:
    """Download and show a preview of an SRT file."""

    content = HttpKitsunekkoSubtitlesClient().file_content(subtitle_id).decode("utf-8-sig")
    try:
        cues = parse_srt(content)
    except Exception as exc:
        console.print(f"[red]Failed to parse SRT: {exc}[/]")
        return

    console.print(f"\n[bold]SRT Preview: {name}[/] ([dim]{len(cues)} cues[/])")
    table = Table()
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Start", style="cyan", width=10)
    table.add_column("End", style="cyan", width=10)
    table.add_column("Text", style="white")
    for cue in cues[:max_cues]:
        table.add_row(str(cue.index), f"{cue.start_s:,.3f}", f"{cue.end_s:,.3f}", cue.text[:80])
    console.print(table)

    total_chars = sum(len(cue.text) for cue in cues)
    kana = sum(1 for cue in cues for char in cue.text if "\u3040" <= char <= "\u30ff")
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
        table.add_row(str(name_full), str(name_native), str(char.get("role", "")))

    console.print(table)

