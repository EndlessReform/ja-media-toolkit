from __future__ import annotations

from textual.app import ComposeResult
from rich.console import Group, RenderableType
from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


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


class ReasonStatsModal(ModalScreen[None]):
    """Show current-track and whole-run cleanup reason counts."""

    CSS = """
    ReasonStatsModal { align: center middle; }
    #reason-stats-dialog {
        width: 92%; height: 88%; padding: 1 2;
        background: $surface; border: tall $accent;
    }
    #reason-stats-content { height: 1fr; }
    #reason-stats-note { height: auto; margin-top: 1; }
    """

    def __init__(self, current: RenderableType, global_: RenderableType) -> None:
        super().__init__()
        self.current = current
        self.global_ = global_

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[b]Edit / remove reasons[/]  [dim](s / Esc / q to close)[/]"),
            VerticalScroll(
                Static(Group(self.current, Text(""), self.global_)),
                id="reason-stats-content",
            ),
            Static(
                "Percentages use cues of that decision type. A multi-reason cue "
                "appears once under each saved reason.",
                id="reason-stats-note",
            ),
            id="reason-stats-dialog",
        )

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key in {"escape", "s", "q"}:
            event.stop()
            self.dismiss(None)


class RuleStatsModal(ModalScreen[None]):
    """Show whole-run scores for the candidate deterministic rules."""

    CSS = """
    RuleStatsModal { align: center middle; }
    #rule-stats-dialog {
        width: 96%; height: 90%; padding: 1 2;
        background: $surface; border: tall $accent;
    }
    #rule-stats-content { height: 1fr; }
    #rule-stats-note { height: auto; margin-top: 1; }
    """

    def __init__(self, content: RenderableType) -> None:
        super().__init__()
        self.content = content

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[b]Candidate rule scores[/]  [dim](R / Esc / q to close)[/]"),
            VerticalScroll(Static(self.content), id="rule-stats-content"),
            Static(
                "Coverage is exact output over saved model edits/removals. "
                "Escalations and missing decisions are unscored.",
                id="rule-stats-note",
            ),
            id="rule-stats-dialog",
        )

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key in {"escape", "R", "q"} or event.character == "R":
            event.stop()
            self.dismiss(None)
