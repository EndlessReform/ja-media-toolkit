"""Navigation, playback, and desktop interaction for subsync."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace

from ja_media_core.proc import run as run_process
from ja_media_core.transcripts import SubtitleCue, shift_srt_cues
from ja_media_frontend.subsync.service import SubtitleTrack
from ja_media_frontend.widgets.timeline import format_clock


class SubsyncInteractionMixin:
    """Keyboard navigation and materialized-audio playback behavior."""

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        key = event.key
        char = event.character
        handled = True
        if char == "q":
            self.stop_playback()
            self.exit()
        elif key == "ctrl+c":
            self.copy_current_subtitle()
        elif key == "f6":
            self.action_open_remote_lookup()
        elif key == "f7":
            self.action_open_remote_file_picker()
        elif key == "space" or char == " ":
            self.toggle_playback()
        elif char == "h":
            self.move_cue(-1)
        elif char == "l":
            self.move_cue(1)
        elif char == "j":
            self.move_track(1)
        elif char == "k":
            self.move_track(-1)
        elif char == "g":
            if self._pending_g:
                self.go_start()
            else:
                self._pending_g = True
                self.set_timer(0.75, self._clear_g_pending)
                self.refresh_view()
        elif char == "G":
            self.go_end()
        elif key in {"ctrl+f", "page_down", "pagedown"}:
            self.page_window(1.0)
        elif key in {"ctrl+b", "page_up", "pageup"}:
            self.page_window(-1.0)
        elif key == "ctrl+d":
            self.page_window(0.5)
        elif key == "ctrl+u":
            self.page_window(-0.5)
        elif char in {"+", "="}:
            self.zoom_window(0.5)
        elif char == "p":
            self.action_promote()
        elif char == "z":
            self.shift_track_timing(-0.1)
        elif char == "x":
            self.shift_track_timing(0.1)
        elif char in {"-", "_"}:
            self.zoom_window(2.0)
        else:
            handled = False
        if handled:
            event.stop()

    @property
    def track(self) -> SubtitleTrack:
        if not self.tracks:
            raise RuntimeError("No subtitle tracks are loaded")
        return self.tracks[self.track_index]

    @property
    def cue_index(self) -> int:
        return self.cue_indices[self.track_index]

    @property
    def current_cue(self) -> SubtitleCue | None:
        if not self.tracks or not self.track.cues:
            return None
        return self.track.cues[self.cue_index]

    def move_cue(self, delta: int) -> None:
        if not self.tracks or not self.track.cues:
            return
        if self.is_playing():
            self.stop_playback()
            self._playback_status = "stopped"
        self.cue_indices[self.track_index] = max(
            0,
            min(len(self.track.cues) - 1, self.cue_index + delta),
        )
        self.ensure_cue_visible()
        self.refresh_view()

    def move_track(self, delta: int) -> None:
        if not self.tracks:
            return
        self.track_index = (self.track_index + delta) % len(self.tracks)
        self.normalize_window()
        self.select_cue_near_time(self.window_start_s + self.window_s / 2)
        self.refresh_view()

    def shift_track_timing(self, offset_s: float) -> None:
        """Shift the selected track's cues by a constant offset."""
        if not self.tracks:
            return
        track = self.track
        if not track.cues:
            return
        shifted_cues = shift_srt_cues(track.cues, offset_s, negative="clamp")
        self.tracks[self.track_index] = replace(track, cues=shifted_cues, modified=True)
        self.refresh_view()

    def page_window(self, pages: float) -> None:
        self.window_start_s += pages * self.window_s
        self.normalize_window()
        self.select_cue_near_time(self.window_start_s + self.window_s / 2)
        self.refresh_view()

    def zoom_window(self, factor: float) -> None:
        center_s = self._focus_time_s()
        self.window_s = max(5.0, min(self.timeline_end_s(), self.window_s * factor))
        self.window_start_s = center_s - self.window_s / 2
        self.normalize_window()
        self.refresh_view()

    def go_start(self) -> None:
        self._pending_g = False
        self.window_start_s = 0.0
        if self.tracks and self.track.cues:
            self.cue_indices[self.track_index] = 0
        self.refresh_view()

    def go_end(self) -> None:
        self._pending_g = False
        if self.tracks and self.track.cues:
            self.cue_indices[self.track_index] = len(self.track.cues) - 1
        self.window_start_s = self.timeline_end_s() - self.window_s
        self.normalize_window()
        self.refresh_view()

    def ensure_cue_visible(self) -> None:
        cue = self.current_cue
        if cue is None:
            return
        if cue.start_s < self.window_start_s:
            self.window_start_s = cue.start_s - self.window_s * 0.15
        elif cue.end_s > self.window_start_s + self.window_s:
            self.window_start_s = cue.end_s - self.window_s * 0.85
        self.normalize_window()

    def select_cue_near_time(self, timestamp_s: float) -> None:
        if not self.tracks:
            return
        cues = self.track.cues
        if not cues:
            self.cue_indices[self.track_index] = 0
            return
        for index, cue in enumerate(cues):
            if cue.end_s >= timestamp_s:
                self.cue_indices[self.track_index] = index
                return
        self.cue_indices[self.track_index] = len(cues) - 1

    def normalize_window(self) -> None:
        max_start_s = max(0.0, self.timeline_end_s() - self.window_s)
        self.window_start_s = max(0.0, min(max_start_s, self.window_start_s))

    def timeline_end_s(self) -> float:
        if not self.tracks:
            return self.window_s
        return max(self.track.end_s, self.window_s)

    def toggle_playback(self) -> None:
        if self.is_playing():
            self.stop_playback()
            self._playback_status = "stopped"
            self.refresh_view()
            return
        cue = self.current_cue
        if cue is None:
            self._playback_status = "no subtitle selected"
            self.refresh_view()
            return
        start_s, duration_s = playback_range(cue)
        try:
            self._player.play(start_s, duration_s)
        except RuntimeError as exc:
            self._playback_status = str(exc)
            self.notify(str(exc), severity="error")
        else:
            end_s = start_s + duration_s
            self._playback_status = (
                f"playing {format_clock(start_s)} -> {format_clock(end_s)}"
            )
            self._stop_playback_poll()
            self._playback_poll = self.set_interval(0.1, self._on_playback_tick)
        self.refresh_view()

    def _on_playback_tick(self) -> None:
        if not self.is_playing():
            self._stop_playback_poll()
            self.refresh_view()

    def _stop_playback_poll(self) -> None:
        if self._playback_poll is not None:
            self._playback_poll.stop()
            self._playback_poll = None

    def stop_playback(self) -> None:
        self._player.stop()
        self._stop_playback_poll()

    def is_playing(self) -> bool:
        return self._player.is_playing()

    def playback_status(self) -> str:
        if self.is_playing():
            return self._playback_status
        if self._playback_status.startswith("playing "):
            self._playback_status = "playback finished"
        return self._playback_status

    def _focus_time_s(self) -> float:
        cue = self.current_cue
        if cue is not None:
            return (cue.start_s + cue.end_s) / 2
        return self.window_start_s + self.window_s / 2

    def _clear_g_pending(self) -> None:
        if self._pending_g:
            self._pending_g = False
            self.refresh_view()


def playback_range(cue: SubtitleCue) -> tuple[float, float]:
    """Return a nonempty playback interval matching one subtitle cue."""

    start_s = max(0.0, cue.start_s)
    end_s = max(start_s, cue.end_s)
    return start_s, max(0.001, end_s - start_s)


def write_clipboard(text: str) -> None:
    """Write text to the user's desktop clipboard using common local tools."""

    command = clipboard_command()
    if command is None:
        raise RuntimeError("clipboard command not found")
    try:
        run_process(
            command,
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("clipboard copy failed") from exc


def clipboard_command() -> list[str] | None:
    """Return the first available command for writing clipboard text."""

    for command in (
        ["pbcopy"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ):
        if shutil.which(command[0]) is not None:
            return command
    return None
