"""Focused Textual dialogs used by the subsync application."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select, Static

from ja_media_frontend.subsync.models import (
    ManualSubtitlePickRequest,
    RemoteLookupRequest,
    RemoteLookupState,
    RemoteSourceKind,
)
from ja_media_frontend.subsync.utils import search_remote_subtitles


class ConfirmOverwriteModal(ModalScreen[bool]):
    """Ask the user whether to overwrite an existing sidecar SRT."""

    CSS = """
    ConfirmOverwriteModal { align: center middle; }
    #confirm-dialog {
        width: 64; height: auto; padding: 1 2;
        background: $surface; border: tall $accent;
    }
    #confirm-path { height: auto; margin-top: 1; color: $text-muted; }
    #confirm-actions { height: auto; margin-top: 1; }
    """

    def __init__(self, destination: Path) -> None:
        super().__init__()
        self.destination = destination

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Overwrite existing subtitle?"),
            Static(str(self.destination), id="confirm-path"),
            Horizontal(
                Button("No", id="confirm-no"),
                Button("Yes", variant="primary", id="confirm-yes"),
                id="confirm-actions",
            ),
            id="confirm-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-no":
            self.dismiss(False)
        elif event.button.id == "confirm-yes":
            self.dismiss(True)


class RemoteLookupModal(ModalScreen[RemoteLookupRequest | None]):
    """Change the Kitsunekko selector while the TUI is running."""

    CSS = """
    RemoteLookupModal { align: center middle; }
    #remote-dialog {
        width: 64; height: auto; padding: 1 2;
        background: $surface; border: tall $accent;
    }
    #remote-dialog Label { margin-top: 1; }
    #remote-actions { height: auto; margin-top: 1; }
    """

    def __init__(self, state: RemoteLookupState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        source = self.state.source or "anilist"
        yield Vertical(
            Label("Kitsunekko lookup"),
            Label("Source"),
            Select(
                [("AniList", "anilist"), ("TVDB", "tvdb")],
                value=source,
                allow_blank=False,
                id="remote-source",
            ),
            Label("Numeric ID"),
            Input(value=str(self.state.external_id or ""), id="remote-id"),
            Label("Episode"),
            Input(value=str(self.state.episode_number or ""), id="remote-episode"),
            Label("TVDB media kind"),
            Input(value=self.state.media_kind or "tv", id="remote-kind"),
            Horizontal(
                Button("Fetch", variant="primary", id="remote-fetch"),
                Button("Cancel", id="remote-cancel"),
                id="remote-actions",
            ),
            id="remote-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "remote-cancel":
            self.dismiss(None)
            return
        if event.button.id == "remote-fetch":
            request = self._request_from_inputs()
            if request is not None:
                self.dismiss(request)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        request = self._request_from_inputs()
        if request is not None:
            self.dismiss(request)

    def _request_from_inputs(self) -> RemoteLookupRequest | None:
        source_value = self.query_one("#remote-source", Select).value
        source: RemoteSourceKind = "tvdb" if source_value == "tvdb" else "anilist"
        external_id = self._positive_input("#remote-id", "ID")
        episode_number = self._positive_input("#remote-episode", "episode")
        if external_id is None or episode_number is None:
            return None
        media_kind = self.query_one("#remote-kind", Input).value.strip() or "tv"
        return RemoteLookupRequest(
            source=source,
            external_id=external_id,
            episode_number=episode_number,
            media_kind=media_kind,
        )

    def _positive_input(self, selector: str, label: str) -> int | None:
        raw_value = self.query_one(selector, Input).value.strip()
        if not raw_value.isdecimal() or int(raw_value) <= 0:
            self.notify(f"{label} must be a positive integer", severity="error")
            return None
        return int(raw_value)


class RemoteFilePickModal(ModalScreen[ManualSubtitlePickRequest | None]):
    """Typeahead dialog for choosing a subtitle from a series inventory."""

    CSS = """
    RemoteFilePickModal { align: center middle; }
    #remote-pick-dialog {
        width: 96; height: 30; padding: 1 2;
        background: $surface; border: tall $accent;
    }
    #remote-pick-message { height: auto; color: $text-muted; }
    #remote-pick-filter { margin-top: 1; margin-bottom: 1; }
    #remote-pick-list { height: 1fr; }
    #remote-pick-actions { height: auto; margin-top: 1; }
    """

    MAX_OPTIONS = 25

    def __init__(self, files: tuple[dict[str, Any], ...], *, message: str) -> None:
        super().__init__()
        self.files = files
        self.message = message
        self.filtered_files: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Choose Kitsunekko subtitle"),
            Static(self.message, id="remote-pick-message"),
            Input(
                placeholder="Filter by filename, group, episode, tags...",
                id="remote-pick-filter",
            ),
            OptionList(id="remote-pick-list"),
            Horizontal(
                Button("Use selected", variant="primary", id="remote-pick-use"),
                Button("Cancel", id="remote-pick-cancel"),
                id="remote-pick-actions",
            ),
            id="remote-pick-dialog",
        )

    def on_mount(self) -> None:
        self._refresh_options("")
        self.query_one("#remote-pick-filter", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "remote-pick-filter":
            self._refresh_options(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "remote-pick-filter":
            self._dismiss_selected()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "remote-pick-list":
            self._dismiss_index(event.index)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "remote-pick-cancel":
            self.dismiss(None)
        elif event.button.id == "remote-pick-use":
            self._dismiss_selected()

    def _refresh_options(self, query: str) -> None:
        self.filtered_files = [
            dict(file)
            for file in search_remote_subtitles(
                self.files,
                query,
                limit=self.MAX_OPTIONS,
            )
        ]
        option_list = self.query_one("#remote-pick-list", OptionList)
        option_list.set_options(
            [_remote_file_option_label(file) for file in self.filtered_files]
        )
        if self.filtered_files:
            option_list.highlighted = 0

    def _dismiss_selected(self) -> None:
        option_list = self.query_one("#remote-pick-list", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            self.notify("No subtitle selected", severity="error")
            return
        self._dismiss_index(highlighted)

    def _dismiss_index(self, index: int) -> None:
        if index < 0 or index >= len(self.filtered_files):
            self.notify("No subtitle selected", severity="error")
            return
        self.dismiss(ManualSubtitlePickRequest(self.filtered_files[index]))


class HelpModal(ModalScreen[None]):
    """F1 cheatsheet documenting every subsync keybinding."""

    CSS = """
    HelpModal { align: center middle; }
    #help-dialog {
        width: 72; height: auto; padding: 1 2;
        background: $surface; border: tall $accent;
    }
    #help-dialog Static { margin-top: 0; }
    #help-dialog .help-section { margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[b]Subsync keybindings[/]  [dim](F1 / Esc / q to close)[/]"),
            Static(
                "[b]Playback[/]\n"
                "  space        play / stop current cue\n"
                "  h / l        previous / next cue\n"
                "  j / k        next / previous SRT candidate\n"
                "  gg / G       jump to first / last cue\n"
                "  q            quit",
                markup=True,
                classes="help-section",
            ),
            Static(
                "[b]Window[/]\n"
                "  Ctrl-f / b    page forward / back\n"
                "  Ctrl-d / u    half-page forward / back\n"
                "  + / -         zoom in / out",
                markup=True,
                classes="help-section",
            ),
            Static(
                "[b]Actions[/]\n"
                "  Ctrl-c        copy current subtitle to clipboard\n"
                "  p             promote selected SRT next to media\n"
                "  F6            Kitsunekko lookup\n"
                "  F7            pick subtitle from series inventory\n"
                "  F1            this help",
                markup=True,
                classes="help-section",
            ),
            id="help-dialog",
        )

    def on_key(self, event: Any) -> None:
        if event.key in {"escape", "f1", "q"}:
            event.stop()
            self.dismiss(None)


def _remote_file_option_label(file: dict[str, Any]) -> Text:
    text = Text()
    episode = file.get("episode_local") or file.get("episode_absolute")
    if episode is not None:
        text.append(f"ep {episode}  ", style="bold cyan")
    text.append(str(file.get("filename") or file.get("repo_path") or "<unnamed>"))
    group = file.get("group_hint")
    if group:
        text.append(f"  [{group}]", style="dim")
    return text
