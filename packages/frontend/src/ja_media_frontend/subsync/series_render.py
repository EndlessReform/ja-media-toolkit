"""Compact Rich episode grids with terminal-width-aware block wrapping."""

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ja_media_frontend.subsync.series_report import SeriesReport


def render_series(report: SeriesReport, console: Console) -> None:
    """Show releases and groups beneath episode IDs, keeping holes visible."""
    console.print(Text(f"{report.title}  ·  AniList {report.anilist_id}  ·  {report.status}", style="bold"))
    aired = "?" if report.aired is None else str(report.aired)
    planned = "?" if report.planned is None else str(report.planned)
    denominator = f"/{aired}" if report.aired != 0 else " (none aired)"
    console.print(
        f"{aired} aired / {planned} planned  ·  "
        f"Subs: [green]{report.covered}{denominator}[/green]  ·  "
        f"2+ groups: [cyan]{report.multiple_groups}{denominator}[/cyan]"
    )
    rows = {row.episode: row for row in report.episodes}
    # Expected episodes form the grid; out-of-range files are appended without
    # allocating thousands of empty cells for a stray filename episode number.
    expected_end = max(report.planned or 0, report.aired or 0)
    if not expected_end and rows:
        expected_end = max(rows) if max(rows) <= 1000 else 0
    numbers = sorted(set(range(1, expected_end + 1)) | rows.keys())
    if not numbers:
        console.print("No numbered subtitle releases.")
    else:
        cell_width = max(3, len(str(max(numbers))) + 1, *(len(str(row.releases)) for row in report.episodes))
        columns = max(1, (console.width - 13) // (cell_width + 2))
        # Keep regular cours aligned; unusual or unknown totals use pane width.
        if report.planned:
            for cour in (12, 13):
                if report.planned % cour == 0:
                    columns = min(columns, cour)
                    break
        for offset in range(0, len(numbers), columns):
            block = numbers[offset:offset + columns]
            table = Table(box=box.SIMPLE_HEAD, padding=(0, 1))
            table.add_column("Episode", style="bold", no_wrap=True)
            releases, groups = [], []
            for episode in block:
                table.add_column(f"{episode:02}" + ("*" if episode == report.aired else ""), justify="right", no_wrap=True)
                row = rows.get(episode)
                absent = "—" if report.aired is not None and episode > report.aired else "[red]0[/red]"
                releases.append(str(row.releases) if row else absent)
                groups.append(str(row.groups) if row and row.groups else ("?" if row else absent))
            table.add_row("Releases", *releases)
            table.add_row("Groups", *groups)
            console.print(table)
    notes = ["* Latest aired   — Upcoming   ? Unnamed group", "Releases = subtitle files; groups = distinct named groups."]
    if report.frontier_estimated:
        notes.append("Aired count estimated from AniList's cached schedule.")
    elif report.aired is None:
        notes.append("Aired count unavailable; summary includes all numbered releases.")
    if report.unassigned:
        notes.append(f"{report.unassigned} files without an assigned episode.")
    console.print(Text("\n".join(notes), style="dim"))
