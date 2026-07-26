"""Subsync-compatible navigation and playback for alignment review."""

from __future__ import annotations

from textual import work

from ja_media_frontend.audio import MaterializedAudioPlayer
from ja_media_frontend.subsync.interaction import playback_range
from ja_media_frontend.widgets.timeline import format_clock

from subtitle_alignment.review_audio import LoadedReviewAudio, load_review_audio
from subtitle_alignment.review_data import append_judgment


class AlignmentReviewInteractionMixin:
    """Keep the established h/l, j/k, paging, zoom, and playback semantics."""

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        key, char = event.key, event.character
        handled = True
        if char == "h": self.move_cue(-1)
        elif char == "l": self.move_cue(1)
        elif char == "j": self.move_variant(1)
        elif char == "k": self.move_variant(-1)
        elif char == "[": self.move_episode(-1)
        elif char == "]": self.move_episode(1)
        elif char == ",": self.move_pair(-1)
        elif char == ".": self.move_pair(1)
        elif key in {"ctrl+f", "page_down", "pagedown"}: self.page_window(1.0)
        elif key in {"ctrl+b", "page_up", "pageup"}: self.page_window(-1.0)
        elif key == "ctrl+d": self.page_window(0.5)
        elif key == "ctrl+u": self.page_window(-0.5)
        elif char in {"+", "="}: self.zoom_window(0.5)
        elif char in {"-", "_"}: self.zoom_window(2.0)
        elif char == "g": self._handle_g()
        elif char == "G": self.go_end()
        elif char == "A": self.fetch_audio()
        elif key == "space" or char == " ": self.toggle_playback()
        elif char in {"1", "2", "3", "4"}: self.save_label(char)
        elif char == "q": self.stop_playback(); self.exit()
        else: handled = False
        if handled: event.stop()

    def move_cue(self, delta: int) -> None:
        if not self._output:
            return
        self.stop_playback()
        key = (self.case.pair_id, self.variant.method)
        self.cue_indices[key] = max(
            0, min(len(self._output) - 1, self.cue_index + delta)
        )
        self.ensure_cue_visible()
        self.refresh_view()

    def move_variant(self, delta: int) -> None:
        center = self.window_start_s + self.window_s / 2
        self.stop_playback()
        self.variant_index = (self.variant_index + delta) % len(self.case.variants)
        self._load_tracks()
        self.select_cue_near(center)
        self.refresh_view()

    def move_pair(self, delta: int) -> None:
        self.stop_playback()
        self.pair_index = (self.pair_index + delta) % len(self.episode_cases)
        self.variant_index = 0
        self.window_start_s = 0.0
        self._load_tracks()

    def move_episode(self, delta: int) -> None:
        target = max(
            0, min(len(self.episode_keys) - 1, self.episode_index + delta)
        )
        self.set_episode_index(target)

    def set_episode_index(self, index: int, *, sync_rail: bool = True) -> None:
        if index == self.episode_index:
            return
        self.stop_playback()
        self._player = None
        self._audio_status = "A fetch audio"
        self.episode_index = index
        self.pair_index = 0
        self.variant_index = 0
        self.window_start_s = 0.0
        if sync_rail:
            self.query_one("#episodes").index = index
        self._load_tracks()

    def ensure_cue_visible(self) -> None:
        cue = self.current_cue
        if cue is None:
            return
        if cue.start_s < self.window_start_s:
            self.window_start_s = cue.start_s - self.window_s * 0.15
        elif cue.end_s > self.window_start_s + self.window_s:
            self.window_start_s = cue.end_s - self.window_s * 0.85
        self.normalize_window()

    def select_cue_near(self, timestamp: float) -> None:
        key = (self.case.pair_id, self.variant.method)
        self.cue_indices[key] = next(
            (index for index, cue in enumerate(self._output) if cue.end_s >= timestamp),
            max(0, len(self._output) - 1),
        )
        self.ensure_cue_visible()

    def page_window(self, pages: float) -> None:
        self.window_start_s += pages * self.window_s
        self.normalize_window()
        self.select_cue_near(self.window_start_s + self.window_s / 2)
        self.refresh_view()

    def zoom_window(self, factor: float) -> None:
        focus = self.current_cue.start_s if self.current_cue else self.window_start_s
        self.window_s = max(5.0, min(self.timeline_end_s(), self.window_s * factor))
        self.window_start_s = focus - self.window_s / 2
        self.normalize_window()
        self.refresh_view()

    def normalize_window(self) -> None:
        self.window_start_s = max(
            0.0,
            min(self.timeline_end_s() - self.window_s, self.window_start_s),
        )

    def timeline_end_s(self) -> float:
        return max(self.window_s, *(cue.end_s for cue in self._output))

    def _handle_g(self) -> None:
        if self._pending_g:
            self._pending_g = False
            self.window_start_s = 0.0
            if self._output:
                self.cue_indices[(self.case.pair_id, self.variant.method)] = 0
            self.refresh_view()
        else:
            self._pending_g = True
            self.set_timer(0.75, self._clear_g)

    def _clear_g(self) -> None:
        self._pending_g = False

    def go_end(self) -> None:
        if self._output:
            self.cue_indices[(self.case.pair_id, self.variant.method)] = (
                len(self._output) - 1
            )
        self.window_start_s = self.timeline_end_s() - self.window_s
        self.refresh_view()

    @work(thread=True, exclusive=True, group="audio")
    def fetch_audio(self) -> None:
        key = self.episode_key
        self.call_from_thread(self._set_audio_status, "fetching + decoding…")
        try:
            loaded = load_review_audio(self.result, *key)
        except Exception as exc:
            self.call_from_thread(self._audio_failed, key, str(exc))
        else:
            self.call_from_thread(self._audio_loaded, key, loaded)

    def _set_audio_status(self, status: str) -> None:
        self._audio_status = status
        self.refresh_view()

    def _audio_failed(self, key, detail: str) -> None:
        if key != self.episode_key:
            return
        self._audio_status = f"unavailable: {detail}"
        self.notify(self._audio_status, severity="error")
        self.refresh_view()

    def _audio_loaded(self, key, loaded: LoadedReviewAudio) -> None:
        if key != self.episode_key:
            return
        self._player = MaterializedAudioPlayer(loaded.audio)
        self._audio_status = loaded.status
        self.notify("Audio ready — Space plays current cue")
        self.refresh_view()

    def toggle_playback(self) -> None:
        if self.is_playing():
            self.stop_playback()
            self._audio_status = "stopped"
            self.refresh_view()
            return
        if self._player is None:
            self.notify(
                "Press A to fetch and decode this episode's audio",
                severity="warning",
            )
            return
        cue = self.current_cue
        if cue is None:
            return
        start, duration = playback_range(cue)
        try:
            self._player.play(start, duration)
        except RuntimeError as exc:
            self.notify(str(exc), severity="error")
            return
        self._audio_status = f"playing {format_clock(start)}"
        self._playback_poll = self.set_interval(0.1, self._playback_tick)
        self.refresh_view()

    def _playback_tick(self) -> None:
        if not self.is_playing():
            self.stop_playback()
            self._audio_status = "ready"
            self.refresh_view()

    def stop_playback(self) -> None:
        if self._player is not None:
            self._player.stop()
        if self._playback_poll is not None:
            self._playback_poll.stop()
            self._playback_poll = None

    def is_playing(self) -> bool:
        return self._player is not None and self._player.is_playing()

    def save_label(self, key: str) -> None:
        label = {
            "1": "anchor_usable",
            "2": "anchor_sparse",
            "3": "candidate_mismatch",
            "4": "needs_audio",
        }[key]
        append_judgment(self.labels, self.case, self.variant, label)
        self.notify(f"saved {label}")
