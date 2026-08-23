from __future__ import annotations

from pathlib import Path
from typing import Callable

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, Footer, Header, Label, Static

from ja_media_frontend.audio import MaterializedAudioPlayer
from ja_media_frontend.srt_cleaning.review_audio import ReviewAudio
from ja_media_frontend.srt_cleaning.review_blind_alignment import (
    BlindAlignmentReviewMixin,
)
from ja_media_frontend.srt_cleaning.review_dialogs import (
    EpisodeSelectModal,
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
)
from ja_media_frontend.srt_cleaning.review_tabs import ReviewTabMixin
from ja_media_frontend.srt_cleaning.review_view_rendering import (
    ReviewViewRenderingMixin,
)
from ja_media_frontend.widgets.timeline import TimelineWidget


class SrtCleaningReviewApp(
    ReviewTabMixin,
    BlindAlignmentReviewMixin,
    ReviewViewRenderingMixin,
    RuleOverlayMixin,
    SrtCleaningReviewInteractionMixin,
    App[None],
):
    """Review original vs cleaned subtitles with optional audio playback."""

    BINDINGS = [
        ("f1", "help", "Help"),
        ("f5", "show_cue_review", "Cue review"),
        ("f6", "show_reason_pivot", "Reason pivot"),
        ("f7", "show_alignment_ab", "Alignment comparison"),
        ("t", "toggle_timing", "Timing borders"),
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
        alignment_eval_path: Path | None = None,
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
        self.alignment_eval_path = alignment_eval_path
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
        self.timing_mode = "aligned"

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
                    yield TimelineWidget(
                        id="timeline", empty_message="No SRTs for episode."
                    )
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
        return self.workspace.sources_for_episode(self.anilist_id, self.episode_number)

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
            use_alignment = self.timing_mode == "aligned"
            timed_cues = tuple(
                cue.cue_with_timing(use_alignment=use_alignment)
                for cue in source.cues
            )
            timeline.set_timeline(
                timed_cues,
                start_s=self.window_start_s,
                duration_s=self.window_s,
                title=f"{source.filename} — {self.timing_mode} borders",
                active_span=(
                    timed_cues[self.cue_index(source)] if timed_cues else None
                ),
                span_styles=(
                    rule_timeline_styles(source.cues)
                    if self.rule_overlay
                    else timeline_styles(source.cues)
                ),
                span_legend=(
                    rule_timeline_legend() if self.rule_overlay else timeline_legend()
                ),
            )
        self.query_one("#diff", Static).update(self.render_diff())
        self.query_one("#help", Static).update(self.render_help())
