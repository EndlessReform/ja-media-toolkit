from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from ja_media_frontend.anilist_batch import (
    is_batch_input,
    run_batch_search,
    search_result_to_mapping,
)
from ja_media_core.anilist_search import (
    HttpAniListSearchClient,
    SearchResponse,
)


def register_get_id_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the AniList title lookup command on the shared frontend parser."""

    search_parser = subparsers.add_parser(
        "get-id",
        help="Search anime by title via the AniList fuzzy-search service",
    )
    search_parser.add_argument(
        "query",
        nargs="?",
        help="Search query (title, romaji, or keywords)",
    )
    search_parser.add_argument("-f", "--file", help="Parse query from file path")
    search_parser.add_argument(
        "-n",
        "--top-k",
        type=int,
        default=3,
        help="Number of results to return. Defaults to 3.",
    )
    search_parser.add_argument(
        "--include-movies",
        action="store_true",
        help="Include movies in search results.",
    )
    search_parser.add_argument(
        "--include-ova",
        action="store_true",
        help="Include OVA entries in search results.",
    )
    search_parser.add_argument(
        "--all-formats",
        action="store_true",
        help="Include all anime formats (specials, music, etc.).",
    )
    search_parser.add_argument(
        "--force-anilist",
        action="store_true",
        help="Query AniList directly instead of the local BM25 mirror.",
    )
    search_parser.add_argument(
        "--field",
        action="append",
        default=[],
        help=(
            "Public AniList metadata field to add to candidates. Repeat for "
            "multiple fields, e.g. --field popularity --field averageScore."
        ),
    )
    search_parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format. Defaults to table.",
    )


def run_search(
    *,
    query: str | None = None,
    file_path: str | None = None,
    top_k: int = 3,
    include_movies: bool = False,
    include_ova: bool = False,
    all_formats: bool = False,
    force_anilist: bool = False,
    extra_fields: tuple[str, ...] = (),
    output_format: str = "table",
) -> None:

    load_dotenv()
    client = HttpAniListSearchClient()

    if file_path:
        path = Path(file_path)
        if query:
            Console(stderr=True).print(
                "[bold red]Error:[/bold red] Provide either a search query OR "
                "a file path (-f), not both."
            )
            return
        if is_batch_input(path):
            try:
                output_path = _run_batch_with_spinner(
                    client=client,
                    path=path,
                    top_k=top_k,
                    include_movies=include_movies,
                    include_ova=include_ova,
                    all_formats=all_formats,
                    force_anilist=force_anilist,
                    extra_fields=extra_fields,
                )
            except ValueError as exc:
                Console(stderr=True).print(f"[bold red]Error:[/bold red] {exc}")
                return
            Console(stderr=True).print(f"Wrote {output_path}")
            return
        query = _query_from_media_filename(path)

    if not query:
        Console(stderr=True).print(
            "[bold red]Error:[/bold red] No search query provided. "
            "Use a positional argument or -f."
        )
        return

    response = client.search(
        query,
        top_k=top_k,
        include_movies=include_movies,
        include_ova=include_ova,
        all_formats=all_formats,
        force_anilist=force_anilist,
        extra_fields=extra_fields,
    )

    if output_format == "json":
        _print_json(response)
    else:
        _print_table(response)


def _run_batch_with_spinner(
    *,
    client: HttpAniListSearchClient,
    path: Path,
    top_k: int,
    include_movies: bool,
    include_ova: bool,
    all_formats: bool,
    force_anilist: bool,
    extra_fields: tuple[str, ...],
) -> Path:
    """Run a batch search under a Rich spinner that surfaces elapsed time.

    Bulk resolution is a single long request that can outrun a fixed read
    timeout; the spinner gives the user visible feedback that the tool is
    still working instead of hanging silently while the SDK waits on the
    service. Output goes to stderr so stdout JSON pipelines stay clean.
    """

    console = Console(stderr=True)
    description = f"Resolving AniList titles from [bold]{path.name}[/bold]"
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(description, total=None)
        try:
            return run_batch_search(
                client=client,
                path=path,
                top_k=top_k,
                include_movies=include_movies,
                include_ova=include_ova,
                all_formats=all_formats,
                force_anilist=force_anilist,
                extra_fields=extra_fields,
            )
        finally:
            progress.update(task, completed=1, total=1)


def _print_table(response: SearchResponse) -> None:
    console = Console()

    if not response.results:
        console.print("[dim]No results found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("ID", justify="right", width=7)
    table.add_column("Title (EN)")
    table.add_column("Title (JP)")
    table.add_column("Title (Romaji)")
    table.add_column("Season", justify="center", width=13)
    table.add_column("Format", justify="center", width=6)
    table.add_column("Score", justify="right", width=8)
    extra_columns = tuple(
        dict.fromkeys(
            field
            for result in response.results
            for field in result.extra_fields
        )
    )
    for column in extra_columns:
        table.add_column(column)

    for r in response.results:
        season_str = ""
        if r.season or r.season_year:
            parts = []
            if r.season:
                parts.append(r.season.title())
            if r.season_year:
                parts.append(str(r.season_year))
            season_str = " ".join(parts)

        row = [
            str(r.anilist_id or "-"),
            r.title_english or "",
            r.title_native or "",
            r.title_romaji or "",
            season_str,
            r.format or "",
            f"{r.score:.2f}",
        ]
        row.extend(_display_value(r.extra_fields.get(column)) for column in extra_columns)
        table.add_row(*row)

    console.print(table)


def _print_json(response: SearchResponse) -> None:
    results: list[dict[str, Any]] = []
    for r in response.results:
        results.append(search_result_to_mapping(r))
    print(json.dumps(results, ensure_ascii=False, indent=2))


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _query_from_media_filename(path: Path) -> str | None:
    import PTN

    stem = path.stem
    parsed = PTN.parse(stem)
    query = parsed.get("title")
    if not query:
        Console(stderr=True).print(
            "[bold red]Error:[/bold red] Could not parse a title from filename "
            f"[yellow]{stem}[/yellow]"
        )
        return None
    return str(query)
