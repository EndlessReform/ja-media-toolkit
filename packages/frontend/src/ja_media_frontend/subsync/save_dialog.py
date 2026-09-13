"""Save-as interaction for subtitles without requiring a media destination."""

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from ja_media_frontend.subsync.dialogs import ConfirmOverwriteModal
from ja_media_frontend.subsync.subtitle_export import save_subtitle


class SaveSubtitleModal(ModalScreen[Path | None]):
    """Choose an explicit SRT destination outside temporary downloads."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    SaveSubtitleModal { align: center middle; }
    #save-dialog { width: 76; height: auto; padding: 1 2;
        background: $surface; border: tall $accent; }
    #save-actions { height: auto; margin-top: 1; }
    """

    def __init__(self, suggested: Path) -> None:
        super().__init__()
        self.suggested = suggested

    def compose(self) -> ComposeResult:
        with Vertical(id="save-dialog"):
            yield Label("Save subtitle as")
            yield Input(str(self.suggested), id="save-path")
            yield Label("", id="save-error")
            with Horizontal(id="save-actions"):
                yield Button("Cancel", id="save-cancel")
                yield Button("Save", id="save-confirm", variant="primary")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.submit_path()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "save-confirm":
            self.submit_path()
        else:
            self.action_cancel()

    def submit_path(self) -> None:
        destination = Path(self.query_one("#save-path", Input).value).expanduser()
        if destination.suffix.lower() != ".srt" or not destination.parent.is_dir():
            self.query_one("#save-error", Label).update(
                "Choose a .srt filename in an existing directory."
            )
            return
        self.dismiss(destination.absolute())


class SubsyncSaveMixin:
    """Export the selected track even when sidecar promotion is unavailable."""

    def action_save_subtitle_as(self) -> None:
        if not self.tracks:
            self.notify("No subtitle tracks loaded", severity="warning")
            return
        track = self.track

        def write(destination: Path, overwrite: bool = False) -> None:
            try:
                save_subtitle(track, destination, overwrite=overwrite)
            except (OSError, ValueError) as exc:
                self.notify(f"Could not save subtitle: {exc}", severity="error")
            else:
                self.notify(f"Saved subtitle to {destination}")

        def selected(destination: Path | None) -> None:
            if destination is None:
                return
            if destination.exists():
                self.push_screen(
                    ConfirmOverwriteModal(destination),
                    lambda confirmed: write(destination, True) if confirmed else None,
                )
            else:
                write(destination)

        self.push_screen(
            SaveSubtitleModal(Path.cwd() / f"{track.stem_label}.srt"), selected
        )
