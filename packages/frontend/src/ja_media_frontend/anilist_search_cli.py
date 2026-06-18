from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from ja_media_core.anilist_search import (
    HttpAniListSearchClient,
    SearchResponse,
)


def run_search(
    *,
    query: str,
    top_k: int = 3,
    include_movies: bool = False,
    include_ova: bool = False,
    all_formats: bool = False,
    output_format: str = "table",
) -> None:

    load_dotenv()

    client = HttpAniListSearchClient()
    response = client.search(
        query,
        top_k=top_k,
        include_movies=include_movies,
        include_ova=include_ova,
        all_formats=all_formats,
    )

    if output_format == "json":
        _print_json(response)
    else:
        _print_table(response)


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

    for r in response.results:
        season_str = ""
        if r.season or r.season_year:
            parts = []
            if r.season:
                parts.append(r.season.title())
            if r.season_year:
                parts.append(str(r.season_year))
            season_str = " ".join(parts)

        table.add_row(
            str(r.anilist_id or "-"),
            r.title_english or "",
            r.title_native or "",
            r.title_romaji or "",
            season_str,
            r.format or "",
            f"{r.score:.2f}",
        )

    console.print(table)


def _print_json(response: SearchResponse) -> None:
    results: list[dict[str, Any]] = []
    for r in response.results:
        results.append({
            "anilist_id": r.anilist_id,
            "title_english": r.title_english,
            "title_native": r.title_native,
            "title_romaji": r.title_romaji,
            "season": r.season,
            "season_year": r.season_year,
            "format": r.format,
            "score": r.score,
        })
    print(json.dumps(results, ensure_ascii=False, indent=2))
