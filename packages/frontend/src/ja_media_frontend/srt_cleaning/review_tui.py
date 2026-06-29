from __future__ import annotations

from pathlib import Path
from typing import Callable

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from ja_media_frontend.audio import MaterializedAudioPlayer
from ja_media_frontend.srt_cleaning.review_audio import ReviewAudio
from ja_media_frontend.srt_cleaning.review_formatting import cleaned_text, decision_style
from ja_media_frontend.srt_cleaning.review_interaction import (
    SrtCleaningReviewInteractionMixin,
)
from ja_media_frontend.srt_cleaning.review_models import (
    ReviewCue,
    ReviewSource,
    ReviewWorkspace,
)
from ja_media_frontend.widgets.timeline import TimelineWidget, format_clock


class EpisodeSelectModal(ModalScreen[int | None]):
    """Prompt for a numeric episode jump."""

    CSS = """
    EpisodeSelectModal { align: center middle; }
    #episode-dialog {
        width: 48; height: auto; padding: 1 2;
        background: $surface; border: tall $accent;
    }
    #episode-actions { height: auto; margin-top: 1; }
    """

    def __init__(self, current: int) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Episode"),
            Input(value=str(self.current), id="episode-number"),
            Horizontal(
                Button("Open", variant="primary", id="episode-open"),
                Button("Cancel", id="episode-cancel"),
                id="episode-actions",
            ),
            id="episode-dialog",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._dismiss_value()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "episode-cancel":
            self.dismiss(None)
        elif event.button.id == "episode-open":
            self._dismiss_value()

    def _dismiss_value(self) -> None:
        raw = self.query_one("#episode-number", Input).value.strip()
        if not raw.isdecimal() or int(raw) <= 0:
            self.notify("Episode must be a positive integer", severity="error")
            return
        self.dismiss(int(raw))


class SrtCleaningReviewApp(SrtCleaningReviewInteractionMixin, App[None]):
    """Review original vs cleaned subtitles with optional audio playback."""

    BINDINGS = [
        ("f1", "help", "Help"),
        ("e", "select_episode", "Episode"),
    ]

    CSS = """
    Screen { layout: vertical; }
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
        audio_loader: Callable[[int], ReviewAudio] | None = None,
    ) -> None:
        super().__init__()
        self.workspace = workspace
        self.series_label = series_label
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
        self.source_index = 0
        self.cue_indices: dict[str, int] = {}
        self.window_start_s = 0.0
        self.window_s = 120.0
        self._playback_status = ""
        self._clipboard_status = ""
        self._playback_poll = None

    @staticmethod
    def episode_modal(current: int) -> EpisodeSelectModal:
        return EpisodeSelectModal(current)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="source")
        yield Static(id="candidates")
        yield TimelineWidget(id="timeline", empty_message="No SRTs for episode.")
        yield Static(id="diff")
        yield Static(id="help")
        yield Footer()

    def on_mount(self) -> None:
        self._prefetch_neighbors()
        self.refresh_view()

    def on_resize(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.is_mounted:
            self.refresh_view()

    def on_unmount(self) -> None:
        self.stop_playback()

    @property
    def episode_sources(self) -> tuple[ReviewSource, ...]:
        return tuple(
            source
            for source in self.workspace.sources
            if source.episode_number == self.episode_number
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
        self.title = f"{self.series_label} - ep {self.episode_number}"
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
            )
        self.query_one("#diff", Static).update(self.render_diff())
        self.query_one("#help", Static).update(self.render_help())

    def render_source(self) -> Text:
        text = Text()
        text.append("AniList: ", style="bold")
        text.append(str(self.workspace.anilist_id), style="cyan")
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
        cue = self.current_cue
        if cue is None:
            return Panel("No cue selected.", title="Original vs cleaned")
        original = cue.original
        decision = cue.decision
        header = Text(
            f"{original.index}  {format_clock(original.start_s)} -> "
            f"{format_clock(original.end_s)}",
            style="bold cyan",
        )
        if self.is_playing():
            header.append(" PLAY", style="bold orange3")
        kind = decision.kind if decision else "missing"
        reason = decision.category if decision and decision.category else "no reason saved"
        if decision and not decision.compliant:
            reason += " (noncompliant row)"
        body = Group(
            header,
            Text.assemble(("decision: ", "bold"), (kind, decision_style(kind)), "  ", ("reason: ", "bold"), reason),
            Text.assemble(("original\n", "bold"), original.text or "<empty cue>"),
            _mechanical_text(cue),
            Text.assemble(("cleaned\n", "bold"), cleaned_text(cue)),
        )
        return Panel(body, title="Original vs cleaned", expand=True)

    def render_help(self) -> str:
        return (
            "space play  c copy JSON  h/l cue  j/k source  bracket keys episode  e jump  "
            "Ctrl-f/b page  Ctrl-d/u half-page  +/- zoom  q quit"
        )


def _mechanical_text(cue: ReviewCue) -> Text:
    if not cue.mechanically_changed:
        return Text("")
    rules = ", ".join(cue.mechanical_rules) or "changed"
    return Text.assemble(
        ("mechanical baseline ", "bold"),
        (f"({rules})\n", "dim"),
        cue.mechanical_text,
    )
