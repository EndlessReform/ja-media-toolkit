"""Artifact-first alignment annotator using the established subsync controls."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from ja_media_frontend.widgets.timeline import TimelineWidget, format_clock

from subtitle_alignment.review_interaction import AlignmentReviewInteractionMixin
from subtitle_alignment.review_data import (
    ReviewCase,
    Variant,
    load_review_cases,
    load_tracks,
    track_stats,
)


EpisodeKey = tuple[int, int]


class AlignmentReviewApp(AlignmentReviewInteractionMixin, App[None]):
    """Inspect one episode, candidate pair, method output, and cue at a time."""

    TITLE = "Gate 1 alignment review"
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #rail { width: 22; border-right: tall $primary; background: $surface; }
    #rail-title { height: 3; padding: 1 1 0 1; color: $accent; text-style: bold; }
    #episodes { height: 1fr; }
    #main { width: 1fr; }
    #status { height: 4; padding: 0 1; background: $surface-darken-1; }
    #methods { height: 9; padding: 0 1; }
    #timeline { height: 10; }
    #active { height: 1fr; min-height: 7; padding: 0 1; }
    #help { height: auto; padding: 0 1; background: $surface; }
    ListView:focus > ListItem.--highlight { background: $accent 25%; }
    """
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, result: Path, *, flagged_only: bool, labels: Path) -> None:
        super().__init__()
        self.result = result
        cases = load_review_cases(result, flagged_only=flagged_only)
        if not cases:
            raise ValueError("no review cases matched the requested filter")
        grouped: dict[EpisodeKey, list[ReviewCase]] = defaultdict(list)
        for case in cases:
            grouped[(case.anilist_id, case.episode)].append(case)
        self.episode_keys = tuple(sorted(grouped))
        self.cases_by_episode = {key: tuple(grouped[key]) for key in self.episode_keys}
        self.labels = labels
        self.episode_index = 0
        self.pair_index = 0
        self.variant_index = 0
        self.cue_indices: dict[tuple[str, str], int] = {}
        self.window_start_s = 0.0
        self.window_s = 120.0
        self._anchor = self._candidate = self._output = ()
        self._player = None
        self._audio_status = "A fetch audio"
        self._playback_poll = None
        self._pending_g = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="rail"):
                yield Label("EPISODES", id="rail-title")
                yield ListView(
                    *(
                        ListItem(Label(self._episode_label(key)), id=f"episode-{index}")
                        for index, key in enumerate(self.episode_keys)
                    ),
                    id="episodes",
                    initial_index=0,
                )
            with Vertical(id="main"):
                yield Static(id="status")
                yield Static(id="methods")
                yield TimelineWidget(id="timeline")
                yield Static(id="active")
                yield Static(id="help")
        yield Footer()

    def on_mount(self) -> None:
        self._load_tracks()

    def on_unmount(self) -> None:
        self.stop_playback()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None or event.item.id is None:
            return
        target = int(event.item.id.removeprefix("episode-"))
        if target != self.episode_index:
            self.set_episode_index(target, sync_rail=False)

    @property
    def episode_key(self) -> EpisodeKey:
        return self.episode_keys[self.episode_index]

    @property
    def episode_cases(self) -> tuple[ReviewCase, ...]:
        return self.cases_by_episode[self.episode_key]

    @property
    def case(self) -> ReviewCase:
        return self.episode_cases[self.pair_index]

    @property
    def variant(self) -> Variant:
        return self.case.variants[self.variant_index]

    @property
    def cue_index(self) -> int:
        return self.cue_indices.get((self.case.pair_id, self.variant.method), 0)

    @property
    def current_cue(self):
        return self._output[self.cue_index] if self._output else None

    def _episode_label(self, key: EpisodeKey) -> str:
        cases = self.cases_by_episode[key]
        flags = sum(case.flagged for case in cases)
        return f"{key[0]}:{key[1]:02d}  {len(cases)} pairs  ⚑{flags}"

    def _load_tracks(self) -> None:
        self._anchor, self._candidate, self._output = load_tracks(
            self.result, self.case, self.variant
        )
        key = (self.case.pair_id, self.variant.method)
        self.cue_indices[key] = min(
            self.cue_indices.get(key, 0), max(0, len(self._output) - 1)
        )
        self.ensure_cue_visible()
        self.refresh_view()

    def refresh_view(self) -> None:
        self.query_one("#status", Static).update(self.render_status())
        self.query_one("#methods", Static).update(self.render_methods())
        self.query_one("#timeline", TimelineWidget).set_timeline(
            self._output,
            start_s=self.window_start_s,
            duration_s=self.window_s,
            title=self.variant.method,
            active_span=self.current_cue,
            reference_spans=self._anchor,
            reference_title="embedded anchor",
        )
        self.query_one("#active", Static).update(self.render_active())
        self.query_one("#help", Static).update(self.render_help())

    def render_status(self) -> Text:
        anchor, candidate = track_stats(self._anchor), track_stats(self._candidate)
        first = Text()
        first.append("SELECTION  ", "bold magenta")
        first.append(f"AniList {self.case.anilist_id}", "bold cyan")
        first.append(f"  EP {self.case.episode:02d}", "bold cyan")
        first.append(f"  PAIR {self.pair_index + 1}/{len(self.episode_cases)}", "bold magenta")
        first.append(f"  {Path(self.case.candidate_repo_path).name}", "bold")
        second = Text()
        second.append("METHOD     ", "bold magenta")
        second.append(self.variant.method, "bold green")
        second.append(f"  score {_number(self.variant.score)}", "cyan")
        second.append(f"  gain {_number(self.variant.gain)}", "green")
        if self.variant.offset_bound_exceeded:
            second.append("  ⚠ cue shift >30s", "bold red")
        second.append(f"  audio: {self._audio_status}", "yellow")
        third = Text("TRACKS     ", style="bold magenta")
        third.append(
            f"anchor {anchor.cues} cues/{anchor.active_s:.0f}s active/{anchor.span_s:.0f}s span"
            f"  candidate {candidate.cues} cues/{candidate.active_s:.0f}s active/"
            f"{candidate.span_s:.0f}s span",
            style="dim",
        )
        return Text("\n").join((first, second, third))

    def render_methods(self) -> Table:
        table = Table(
            title=(
                f"Method {self.variant_index + 1}/{len(self.case.variants)}"
                f" — pair {self.pair_index + 1}/{len(self.episode_cases)}"
            ),
            expand=True,
        )
        table.add_column("", width=2)
        table.add_column("Method", style="cyan")
        table.add_column("Score/gain", justify="right")
        table.add_column("Median shift", justify="right")
        table.add_column("Blocks", justify="right")
        start = max(
            0,
            min(len(self.case.variants) - 5, self.variant_index - 2),
        )
        for index, variant in enumerate(
            self.case.variants[start : start + 5], start=start
        ):
            style = "bold white on dark_green" if index == self.variant_index else ""
            marker = "▶" if index == self.variant_index else ""
            table.add_row(
                marker,
                variant.method,
                f"{_number(variant.score)}/{_number(variant.gain)}",
                _number(variant.median_offset_s, 1),
                str(variant.offset_blocks or 0),
                style=style,
            )
        return table

    def render_active(self) -> Panel:
        cue = self.current_cue
        if cue is None:
            return Panel("No cues in selected output.", title="Current cue")
        text = Text()
        text.append(
            f"{self.cue_index + 1}/{len(self._output)}  "
            f"{format_clock(cue.start_s)} → {format_clock(cue.end_s)}",
            "bold cyan",
        )
        if self.is_playing():
            text.append("  ▶ playing", "bold orange3")
        text.append("\n" + (cue.text or "<empty cue>"))
        return Panel(text, title="Current cue", expand=True)

    def render_help(self) -> Text:
        text = Text()
        for label, keys in (
            ("NAV", "h/l cue  j/k method  [/ ] episode  ,/. pair"),
            ("WINDOW", "Ctrl-f/b page  Ctrl-d/u half-page  +/- zoom  gg/G ends"),
            ("INSPECT", "v full raw anchor/candidate tracks"),
            ("AUDIO", "A fetch+decode  Space play/stop current cue"),
            ("LABEL", "1 usable anchor  2 sparse anchor  3 mismatch  4 needs audio"),
        ):
            if text:
                text.append("\n")
            text.append(f"{label:<7}", "bold magenta")
            text.append(keys, "dim")
        return text

def run_annotator(result: Path, *, flagged_only: bool, labels: Path) -> None:
    """Launch the local diagnostic view over precomputed artifacts."""

    AlignmentReviewApp(result, flagged_only=flagged_only, labels=labels).run()


def _number(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"
