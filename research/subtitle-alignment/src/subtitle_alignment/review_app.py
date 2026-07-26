"""Small diagnostic annotator over precomputed Gate 1 matrix artifacts."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static

from subtitle_alignment.review_data import (
    ReviewCase,
    Variant,
    append_judgment,
    load_review_cases,
    load_tracks,
    track_stats,
)
from subtitle_alignment.review_timeline import ReviewTimeline, clock


class AlignmentReviewApp(App):
    """Inspect sparse anchors and realized transforms before adding audio."""

    TITLE = "Gate 1 alignment diagnostics"
    CSS = """
    Screen { layout: vertical; }
    #facts { height: auto; padding: 0 2; }
    ReviewTimeline { height: 11; }
    #cues { height: 1fr; padding: 0 2; overflow-y: auto; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "next_pair", "Next pair"),
        Binding("k", "previous_pair", "Previous pair"),
        Binding("]", "next_variant", "Next method"),
        Binding("[", "previous_variant", "Previous method"),
        Binding("h", "pan(-1)", "Earlier"),
        Binding("l", "pan(1)", "Later"),
        Binding("plus", "zoom(0.5)", "Zoom in"),
        Binding("minus", "zoom(2)", "Zoom out"),
        Binding("f", "full_episode", "Full episode"),
        Binding("b", "next_boundary", "Next offset block"),
        Binding("a", "label('anchor_usable')", "Usable anchor"),
        Binding("s", "label('anchor_sparse')", "Sparse anchor"),
        Binding("m", "label('candidate_mismatch')", "Mismatch"),
        Binding("u", "label('needs_audio')", "Needs audio"),
    ]

    def __init__(self, result: Path, *, flagged_only: bool, labels: Path) -> None:
        super().__init__()
        self.result = result
        self.cases = load_review_cases(result, flagged_only=flagged_only)
        if not self.cases:
            raise ValueError("no review cases matched the requested filter")
        self.labels = labels
        self.case_index = 0
        self.variant_index = 0
        self.window_start_s = 0.0
        self.window_duration_s = 120.0
        self._anchor = self._candidate = self._output = ()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="facts", markup=False)
        yield ReviewTimeline()
        yield Static(id="cues", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._load()

    @property
    def case(self) -> ReviewCase:
        return self.cases[self.case_index]

    @property
    def variant(self) -> Variant:
        return self.case.variants[self.variant_index]

    def _load(self) -> None:
        self._anchor, self._candidate, self._output = load_tracks(
            self.result, self.case, self.variant
        )
        self._refresh()

    def _refresh(self) -> None:
        anchor_stats = track_stats(self._anchor)
        candidate_stats = track_stats(self._candidate)
        variant = self.variant
        flag = "YES: cue shift >30s" if variant.offset_bound_exceeded else "no"
        self.query_one("#facts", Static).update(
            f"pair {self.case_index + 1}/{len(self.cases)}  {self.case.pair_id}  "
            f"AniList {self.case.anilist_id} ep {self.case.episode}  "
            f"identity decile {self.case.identity_decile}\n"
            f"method {self.variant_index + 1}/{len(self.case.variants)}: "
            f"{variant.method}  status={variant.status}  score={_number(variant.score)}  "
            f"gain={_number(variant.gain)}  >30s flag={flag}\n"
            f"realized scale={_number(variant.scale, 6)}  "
            f"offset median/min/max={_number(variant.median_offset_s)}/"
            f"{_number(variant.min_offset_s)}/{_number(variant.max_offset_s)} s  "
            f"blocks={variant.offset_blocks or 0}\n"
            f"anchor: {anchor_stats.cues} cues, {anchor_stats.active_s:.1f}s active, "
            f"{anchor_stats.span_s:.1f}s span  |  candidate: {candidate_stats.cues} cues, "
            f"{candidate_stats.active_s:.1f}s active, {candidate_stats.span_s:.1f}s span\n"
            f"source: {self.case.candidate_repo_path}"
        )
        self.query_one(ReviewTimeline).set_timeline(
            self._output,
            start_s=self.window_start_s,
            duration_s=self.window_duration_s,
            title=f"anchor vs {variant.method}",
            reference_spans=self._anchor,
        )
        self.query_one("#cues", Static).update(self._visible_cues())

    def _visible_cues(self) -> str:
        end_s = self.window_start_s + self.window_duration_s
        lines = []
        for cue in self._output:
            if cue.end_s < self.window_start_s or cue.start_s > end_s:
                continue
            text = " ".join(str(cue.text).split())
            lines.append(f"{clock(cue.start_s)}–{clock(cue.end_s)}  {text}")
            if len(lines) == 14:
                lines.append("…")
                break
        return "\n".join(lines) or "No selected-output cues in this window."

    def action_next_pair(self) -> None:
        self.case_index = (self.case_index + 1) % len(self.cases)
        self.variant_index = 0
        self._load()
        self.action_full_episode()

    def action_previous_pair(self) -> None:
        self.case_index = (self.case_index - 1) % len(self.cases)
        self.variant_index = 0
        self._load()
        self.action_full_episode()

    def action_next_variant(self) -> None:
        self.variant_index = (self.variant_index + 1) % len(self.case.variants)
        self._load()

    def action_previous_variant(self) -> None:
        self.variant_index = (self.variant_index - 1) % len(self.case.variants)
        self._load()

    def action_pan(self, direction: int) -> None:
        step = self.window_duration_s * 0.75 * direction
        self.window_start_s = max(0.0, self.window_start_s + step)
        self._refresh()

    def action_zoom(self, factor: float) -> None:
        center = self.window_start_s + self.window_duration_s / 2
        self.window_duration_s = min(7200.0, max(10.0, self.window_duration_s * factor))
        self.window_start_s = max(0.0, center - self.window_duration_s / 2)
        self._refresh()

    def action_full_episode(self) -> None:
        self.window_start_s = 0.0
        ends = [cue.end_s for cues in (self._anchor, self._candidate, self._output) for cue in cues]
        self.window_duration_s = max(10.0, max(ends, default=120.0))
        self._refresh()

    def action_next_boundary(self) -> None:
        starts = self.variant.block_starts_s
        target = next((value for value in starts if value > self.window_start_s + 1.0), starts[0] if starts else 0.0)
        self.window_duration_s = min(self.window_duration_s, 120.0)
        self.window_start_s = max(0.0, target - 15.0)
        self._refresh()

    def action_label(self, label: str) -> None:
        append_judgment(self.labels, self.case, self.variant, label)
        self.notify(f"saved {label}: {self.case.pair_id}/{self.variant.method}")


def run_annotator(result: Path, *, flagged_only: bool, labels: Path) -> None:
    """Launch the local artifact-only diagnostic view."""

    AlignmentReviewApp(result, flagged_only=flagged_only, labels=labels).run()


def _number(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"
