from __future__ import annotations

from pathlib import Path
from typing import Callable

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, Footer, Header, Label, Static

from ja_media_frontend.audio import MaterializedAudioPlayer
from ja_media_frontend.srt_cleaning.review_audio import ReviewAudio
from ja_media_frontend.srt_cleaning.review_dialogs import (
    EpisodeSelectModal,
    ReasonStatsModal,
)
from ja_media_frontend.srt_cleaning.review_formatting import (
    timeline_legend,
    timeline_styles,
)
from ja_media_frontend.srt_cleaning.review_interaction import (
    SrtCleaningReviewInteractionMixin,
)
from ja_media_frontend.srt_cleaning.review_models import (
    ReviewCue,
    ReviewSource,
    ReviewWorkspace,
)
from ja_media_frontend.srt_cleaning.review_reason_pivot import ReasonPivotExplorer
from ja_media_frontend.srt_cleaning.review_rail import ReviewEpisodeRail
from ja_media_frontend.srt_cleaning.review_rule_comparison import (
    rule_timeline_legend,
    rule_timeline_styles,
)
from ja_media_frontend.srt_cleaning.review_rule_overlay import (
    RuleOverlayMixin,
    render_cue_panel,
)
from ja_media_frontend.srt_cleaning.review_stats import (
    render_reason_stats,
    summarize_reasons,
)
from ja_media_frontend.srt_cleaning.review_tabs import ReviewTabMixin
from ja_media_frontend.widgets.timeline import TimelineWidget, format_clock


class SrtCleaningReviewApp(
    ReviewTabMixin,
    RuleOverlayMixin,
    SrtCleaningReviewInteractionMixin,
    App[None],
):
    """Review original vs cleaned subtitles with optional audio playback."""

    BINDINGS = [
        ("f1", "help", "Help"),
        ("f5", "show_cue_review", "Cue review"),
        ("f6", "show_reason_pivot", "Reason pivot"),
        ("e", "select_episode", "Episode"),
        ("s", "show_stats", "Stats"),
        ("r", "toggle_rule_overlay", "Rule overlay"),
        ("R", "show_rule_stats", "Rule scores"),
        ("f", "next_flagged", "Next flag"),
        ("F", "previous_flagged", "Previous flag"),
    ]

    CSS = """
    Screen { layout: vertical; }
    #review-tabs { height: auto; padding: 0 1; background: $surface; }
    #review-views, #cue-review { height: 1fr; }
    #rail { width: 34; border-right: tall $primary; background: $surface; }
    #rail-title { height: 3; padding: 1 1 0 1; color: $accent; text-style: bold; }
    #episodes { height: 1fr; }
    #main { width: 1fr; }
    #source { height: auto; padding: 0 1; background: $surface; }
    #candidates { height: auto; max-height: 10; padding: 0 1; margin-bottom: 1; }
    #diff { height: 1fr; min-height: 12; padding: 0 1; }
    #help { height: auto; padding: 0 1; background: $surface; color: $text-muted; }
    """

    TITLE = "ja-media srt-clean review"

    def __init__(
        self,
        *,
        workspace: ReviewWorkspace,
        series_label: str,
        initial_episode: int,
        audio_profile: str,
        manual_audio: Path | None,
        initial_audio: ReviewAudio,
        initial_anilist_id: int | None = None,
        audio_loader: Callable[[int], ReviewAudio] | None = None,
    ) -> None:
        super().__init__()
        self.workspace = workspace
        self.series_label = series_label
        self.anilist_id = initial_anilist_id or workspace.anilist_id
        self.episode_number = initial_episode
        self.audio_profile = audio_profile
        self.manual_audio = manual_audio
        self._audio_loader = audio_loader
        self._audio = initial_audio.materialized
        self._audio_status = initial_audio.status
        self._player = (
            MaterializedAudioPlayer(initial_audio.materialized)
            if initial_audio.materialized is not None
            else None
        )
        self.source_index = workspace.preferred_source_index(
            self.anilist_id, self.episode_number
        )
        self.cue_indices = workspace.preferred_cue_indices()
        self.window_start_s = 0.0
        self.window_s = 120.0
        self._playback_status = ""
        self._clipboard_status = ""
        self._playback_poll = None
        self.rule_overlay = False

    @staticmethod
    def episode_modal(current: int) -> EpisodeSelectModal:
        return EpisodeSelectModal(current)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(self.render_tab_bar("cue-review"), id="review-tabs")
        initial_key = (self.anilist_id, self.episode_number)
        initial_index = (
            self.workspace.episode_keys.index(initial_key)
            if initial_key in self.workspace.episode_keys
            else 0
        )
        with ContentSwitcher(initial="cue-review", id="review-views"):
            with Horizontal(id="cue-review"):
                with Vertical(id="rail"):
                    yield Label("SERIES / EPISODES", id="rail-title")
                    yield ReviewEpisodeRail(
                        self.workspace,
                        initial_index=initial_index,
                        id="episodes",
                    )
                with Vertical(id="main"):
                    yield Static(id="source")
                    yield Static(id="candidates")
                    yield TimelineWidget(id="timeline", empty_message="No SRTs for episode.")
                    yield Static(id="diff")
                    yield Static(id="help")
            yield ReasonPivotExplorer(self.workspace, id="reason-pivot")
        yield Footer()

    def on_mount(self) -> None:
        self._prefetch_neighbors()
        self.refresh_view()

    def on_resize(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.is_mounted:
            self.refresh_view()

    def on_unmount(self) -> None:
        self.stop_playback()

    def on_list_view_highlighted(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.item is None or event.item.id is None:
            return
        index = int(event.item.id.removeprefix("episode-"))
        anilist_id, episode = self.workspace.episode_keys[index]
        self.set_episode_key(anilist_id, episode, sync_rail=False)

    @property
    def episode_sources(self) -> tuple[ReviewSource, ...]:
        return self.workspace.sources_for_episode(
            self.anilist_id, self.episode_number
        )

    @property
    def source(self) -> ReviewSource | None:
        sources = self.episode_sources
        if not sources:
            return None
        self.source_index = min(self.source_index, len(sources) - 1)
        return sources[self.source_index]

    @property
    def current_cue(self) -> ReviewCue | None:
        source = self.source
        if source is None or not source.cues:
            return None
        return source.cues[self.cue_index(source)]

    def cue_index(self, source: ReviewSource) -> int:
        return min(self.cue_indices.get(source.subtitle_id, 0), len(source.cues) - 1)

    def refresh_view(self) -> None:
        series = (
            self.series_label
            if len({source.anilist_id for source in self.workspace.sources}) == 1
            else f"AniList {self.anilist_id}"
        )
        self.title = f"{series} - ep {self.episode_number}"
        self.query_one("#source", Static).update(self.render_source())
        self.query_one("#candidates", Static).update(self.render_candidates())
        timeline = self.query_one("#timeline", TimelineWidget)
        source = self.source
        if source is None:
            timeline.set_timeline((), start_s=0, duration_s=self.window_s)
        else:
            timeline.set_timeline(
                source.cues,
                start_s=self.window_start_s,
                duration_s=self.window_s,
                title=source.filename,
                active_span=self.current_cue,
                span_styles=(
                    rule_timeline_styles(source.cues)
                    if self.rule_overlay
                    else timeline_styles(source.cues)
                ),
                span_legend=(
                    rule_timeline_legend()
                    if self.rule_overlay
                    else timeline_legend()
                ),
            )
        self.query_one("#diff", Static).update(self.render_diff())
        self.query_one("#help", Static).update(self.render_help())

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
                Text(">" if selected else " ", style="bold yellow" if selected else "dim"),
                Text(source.label, style="bold cyan" if selected else ""),
                Text(cue_label, style="bold" if selected else ""),
                str(source.changed_count),
                format_clock(source.end_s),
            )
        if not table.rows:
            table.add_row(" ", f"No reviewable SRTs for episode {self.episode_number}", "-", "0", "-")
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
            "r rule overlay  R rule scores  s reason stats  Ctrl-f/b page  "
            "f/F next/previous flag  "
            "Ctrl-d/u half-page  +/- zoom  q quit"
        )

    def action_show_stats(self) -> None:
        source = self.source
        current_sources = (source,) if source is not None else ()
        current_title = f"Current track: {source.filename}" if source else "Current track"
        self.push_screen(
            ReasonStatsModal(
                render_reason_stats(
                    current_title,
                    summarize_reasons(current_sources),
                ),
                render_reason_stats(
                    f"Whole run: {self.workspace.run_id}",
                    summarize_reasons(self.workspace.sources),
                ),
            )
        )
