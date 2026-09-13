"""Media-free subtitle inspection through the existing application clients."""

import argparse
import json
import sys
import time
from dataclasses import asdict

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from ja_media_core.anilist_search import HttpAniListSearchClient, SearchResponse
from ja_media_core.kitsunekko import HttpKitsunekkoSubtitlesClient
from ja_media_frontend.anilist_search_cli import add_title_search_options
from ja_media_frontend.subsync.series_render import render_series
from ja_media_frontend.subsync.series_report import build_report

METADATA_FIELDS = (
    "title_romaji", "title_english", "title_native", "status", "episodes", "nextAiringEpisode",
)


def register_get_series_parser(commands: argparse._SubParsersAction) -> None:
    """Register a subtitle-only inspection command under subsync."""
    parser = commands.add_parser("get-series", help="Show subtitle releases and groups by episode")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("query", nargs="?", help="Anime title to search")
    selection.add_argument("--anilist-id", "--anilist", type=int, help="Exact AniList ID")
    add_title_search_options(parser)
    parser.add_argument("--format", choices=("table", "json"), default="table")


def select_candidate(response: SearchResponse, console: Console, *, interactive: bool):
    """Require explicit selection when several title matches are returned."""
    candidates = [result for result in response.results if result.anilist_id is not None]
    if not candidates:
        raise ValueError("No matching titles found")
    if len(candidates) == 1:
        return candidates[0]
    table = Table("AniList ID", "Title", "Format", "Season")
    for result in candidates:
        table.add_row(
            str(result.anilist_id),
            result.title_romaji or result.title_english or result.title_native or "",
            result.format or "",
            f"{result.season or ''} {result.season_year or ''}".strip(),
        )
    console.print(table)
    if not interactive:
        raise ValueError("Several titles matched; rerun with --anilist-id from the table")
    selected = Prompt.ask("AniList ID", choices=[str(result.anilist_id) for result in candidates], console=console)
    return next(result for result in candidates if str(result.anilist_id) == selected)


def run_get_series(args: argparse.Namespace) -> None:
    """Fetch metadata and inventory, then render one Rich or JSON report."""
    if args.anilist_id is not None and args.anilist_id <= 0:
        raise SystemExit("--anilist-id must be positive")
    if args.top_k <= 0:
        raise SystemExit("--top-k must be positive")
    if args.force_anilist and args.anilist_id is not None:
        raise SystemExit("--force-anilist requires a title query; exact-ID lookup uses the existing metadata cache")
    load_dotenv()
    console = Console()
    diagnostics = Console(stderr=True)
    try:
        client = HttpAniListSearchClient()
        if args.query is not None:
            if not args.query.strip():
                raise ValueError("Provide a nonempty title query")
            response = client.search(
                args.query, top_k=args.top_k, include_movies=args.include_movies,
                include_ova=args.include_ova, all_formats=args.all_formats,
                force_anilist=args.force_anilist, extra_fields=METADATA_FIELDS[3:],
            )
            selected = select_candidate(response, diagnostics, interactive=sys.stdin.isatty())
            anilist_id = selected.anilist_id
            metadata = dict(selected.extra_fields)
            metadata.update({key: getattr(selected, key) for key in METADATA_FIELDS[:3]})
        else:
            anilist_id = args.anilist_id
            metadata = client.anime(anilist_id, fields=METADATA_FIELDS).fields
        inventory = HttpKitsunekkoSubtitlesClient().anilist_files(anilist_id)
        report = build_report(anilist_id, metadata, inventory.files, now=time.time())
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        raise SystemExit(f"Could not inspect series: {exc}") from exc
    if args.format == "json":
        payload = asdict(report)
        payload.update(covered=report.covered, multiple_groups=report.multiple_groups)
        console.print_json(json.dumps(payload, ensure_ascii=False))
    else:
        render_series(report, console)
