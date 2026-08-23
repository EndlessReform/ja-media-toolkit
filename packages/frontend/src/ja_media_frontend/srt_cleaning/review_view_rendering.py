"""Rendering methods for the main subtitle-cleaning review view."""

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from ja_media_frontend.srt_cleaning.review_dialogs import ReasonStatsModal
from ja_media_frontend.srt_cleaning.review_rule_overlay import render_cue_panel
from ja_media_frontend.srt_cleaning.review_stats import (
    render_reason_stats,
    summarize_reasons,
)
from ja_media_frontend.widgets.timeline import format_clock


class ReviewViewRenderingMixin:
    """Render source metadata, candidate tracks, cue details, and help."""

    def render_source(self) -> Text:
        text = Text()
        text.append("AniList: ", style="bold")
        text.append(str(self.anilist_id), style="cyan")
        text.append(f"  run: {self.workspace.run_id}")
        text.append(f"  episode: {self.episode_number}", style="bold")
        text.append("  ")
        text.append(self._audio_status, style="dim")
        if self.playback_status():
            text.append("  ")
            text.append(self.playback_status(), style="orange3")
        if self._clipboard_status:
            text.append("  ")
            text.append(self._clipboard_status, style="dim")
        if self.rule_overlay:
            text.append("  rule overlay on", style="bold cyan")
        return text

    def render_candidates(self) -> Table:
        table = Table(expand=True, box=None, show_edge=False, pad_edge=False)
        table.add_column("", width=1)
        table.add_column("source", ratio=1, overflow="ellipsis", no_wrap=True)
        table.add_column("cue", justify="right", no_wrap=True)
        table.add_column("changed", justify="right", no_wrap=True)
        table.add_column("span", justify="right", no_wrap=True)
        for index, source in enumerate(self.episode_sources):
            selected = index == self.source_index
            cue_label = f"{self.cue_index(source) + 1}/{len(source.cues)}"
            table.add_row(
                Text(
                    ">" if selected else " ", style="bold yellow" if selected else "dim"
                ),
                Text(source.label, style="bold cyan" if selected else ""),
                Text(cue_label, style="bold" if selected else ""),
                str(source.changed_count),
                format_clock(source.end_s),
            )
        if not table.rows:
            table.add_row(
                " ",
                f"No reviewable SRTs for episode {self.episode_number}",
                "-",
                "0",
                "-",
            )
        return table

    def render_diff(self) -> Panel:
        return render_cue_panel(
            self.current_cue,
            playing=self.is_playing(),
            rule_overlay=self.rule_overlay,
        )

    def render_help(self) -> str:
        return (
            "space play  c copy JSON  h/l cue  n/N next/previous non-accept  "
            "j/k source  bracket keys series/episode  e episode jump  "
            "F7 blind alignment A/B  r rule overlay  R rule scores  "
            "s reason stats  Ctrl-f/b page  f/F next/previous flag  "
            "Ctrl-d/u half-page  +/- zoom  q quit"
        )

    def action_show_stats(self) -> None:
        source = self.source
        current_sources = (source,) if source is not None else ()
        current_title = (
            f"Current track: {source.filename}" if source else "Current track"
        )
        self.push_screen(
            ReasonStatsModal(
                render_reason_stats(current_title, summarize_reasons(current_sources)),
                render_reason_stats(
                    f"Whole run: {self.workspace.run_id}",
                    summarize_reasons(self.workspace.sources),
                ),
            )
        )
