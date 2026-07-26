"""Whole-file anchor/candidate comparison for upstream failure diagnosis."""

from __future__ import annotations

from collections.abc import Iterable

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea

from ja_media_core.transcripts import SubtitleCue
from ja_media_frontend.widgets.timeline import format_clock


class FullTrackComparisonModal(ModalScreen[None]):
    """Show both complete source tracks without alignment-method transforms."""

    CSS = """
    FullTrackComparisonModal { align: center middle; }
    #full-track-dialog {
        width: 96%; height: 94%; padding: 1;
        background: $surface; border: tall $accent;
    }
    #full-track-header { height: 2; color: $text; }
    #full-track-columns { height: 1fr; }
    .full-track-column { width: 1fr; height: 1fr; }
    .full-track-title { height: 2; padding: 0 1; text-style: bold; }
    #full-anchor-title { color: cyan; }
    #full-candidate-title { color: magenta; }
    #full-anchor { border: tall cyan; }
    #full-candidate { border: tall magenta; }
    #full-track-help { height: 1; text-align: center; color: $text-muted; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(
        self,
        *,
        anchor: tuple[SubtitleCue, ...],
        candidate: tuple[SubtitleCue, ...],
        anchor_name: str,
        candidate_name: str,
    ) -> None:
        super().__init__()
        self.anchor = anchor
        self.candidate = candidate
        self.anchor_name = anchor_name
        self.candidate_name = candidate_name

    def compose(self) -> ComposeResult:
        header = Text("FULL SOURCE TRACKS", style="bold")
        header.append("  raw inputs; no retiming applied", style="dim")
        yield Vertical(
            Static(header, id="full-track-header"),
            Horizontal(
                Vertical(
                    Static(
                        f"ANCHOR · {len(self.anchor)} cues · {self.anchor_name}",
                        id="full-anchor-title",
                        classes="full-track-title",
                    ),
                    _track_area(self.anchor, "full-anchor"),
                    classes="full-track-column",
                ),
                Vertical(
                    Static(
                        f"CANDIDATE · {len(self.candidate)} cues · {self.candidate_name}",
                        id="full-candidate-title",
                        classes="full-track-title",
                    ),
                    _track_area(self.candidate, "full-candidate"),
                    classes="full-track-column",
                ),
                id="full-track-columns",
            ),
            Static(
                "Tab / Shift-Tab switch panes · arrows/Page/Home/End scroll · Esc close",
                id="full-track-help",
            ),
            id="full-track-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#full-anchor", TextArea).focus()

    def action_close(self) -> None:
        self.dismiss(None)


def format_full_track(cues: Iterable[SubtitleCue]) -> str:
    """Render every cue with an explicit ordinal and source timestamps."""

    blocks = []
    for ordinal, cue in enumerate(cues, start=1):
        blocks.append(
            f"[{ordinal:04d}]  {format_clock(cue.start_s)} → "
            f"{format_clock(cue.end_s)}\n{cue.text or '<empty cue>'}"
        )
    return "\n\n".join(blocks) or "<no cues>"


def _track_area(cues: tuple[SubtitleCue, ...], widget_id: str) -> TextArea:
    return TextArea(
        format_full_track(cues),
        id=widget_id,
        read_only=True,
        show_cursor=False,
        show_line_numbers=False,
        soft_wrap=True,
        tab_behavior="focus",
        highlight_cursor_line=False,
    )
