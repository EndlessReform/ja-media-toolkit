"""Preview the production subsync get-series renderer with synthetic data.

Run from packages/frontend:
uv run python ../../scripts/exploration/subtitle_coverage_mock.py
"""

from rich.console import Console

from ja_media_frontend.subsync.series_render import render_series
from ja_media_frontend.subsync.series_report import build_report


def render(console: Console) -> None:
    """Show the same gapped example through the actual report reducer and grid."""
    groups_by_episode = {
        1: ["A", "B"], 2: ["A", "A", "B"], 3: ["A", None],
        5: ["A", "B"], 6: ["A"], 7: [None, None],
    }
    files = [
        {"subtitle_id": f"{episode}-{index}", "episode_local": episode, "group_hint": group}
        for episode, groups in groups_by_episode.items()
        for index, group in enumerate(groups)
    ]
    report = build_report(123456, {
        "title_romaji": "Example Series", "status": "RELEASING", "episodes": 12,
        "nextAiringEpisode": {"episode": 9, "airingAt": 200},
    }, files, now=100)
    render_series(report, console)
    console.print("[dim]Synthetic preview.[/dim]")


if __name__ == "__main__":
    render(Console())
