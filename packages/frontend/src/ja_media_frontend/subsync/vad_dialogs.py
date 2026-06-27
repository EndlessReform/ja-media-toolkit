"""Focused Textual dialogs for VAD review controls."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class VadThresholdModal(ModalScreen[float | None]):
    """Let the user retune the VAD threshold while reviewing subtitles."""

    CSS = """
    VadThresholdModal { align: center middle; }
    #vad-threshold-dialog {
        width: 48; height: auto; padding: 1 2;
        background: $surface; border: tall $accent;
    }
    #vad-threshold-help { height: auto; color: $text-muted; margin-top: 1; }
    #vad-threshold-actions { height: auto; margin-top: 1; }
    """

    def __init__(self, threshold: float) -> None:
        super().__init__()
        self.threshold = threshold

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("VAD threshold"),
            Input(value=f"{self.threshold:.2f}", id="vad-threshold-input"),
            Static("Use a value from 0.00 to 1.00.", id="vad-threshold-help"),
            Horizontal(
                Button("Apply", variant="primary", id="vad-threshold-apply"),
                Button("Cancel", id="vad-threshold-cancel"),
                id="vad-threshold-actions",
            ),
            id="vad-threshold-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#vad-threshold-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "vad-threshold-cancel":
            self.dismiss(None)
        elif event.button.id == "vad-threshold-apply":
            self._dismiss_value()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "vad-threshold-input":
            self._dismiss_value()

    def _dismiss_value(self) -> None:
        raw_value = self.query_one("#vad-threshold-input", Input).value.strip()
        try:
            threshold = float(raw_value)
        except ValueError:
            self.notify("Threshold must be a number", severity="error")
            return
        if not 0.0 <= threshold <= 1.0:
            self.notify("Threshold must be between 0.00 and 1.00", severity="error")
            return
        self.dismiss(threshold)
