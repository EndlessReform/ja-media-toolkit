from __future__ import annotations

from ja_media_frontend.audio import MaterializedAudioPlayer
from ja_media_frontend.srt_cleaning.review_audio import (
    ReviewAudio,
    load_review_audio,
    prefetch_neighbor_audio,
)
from ja_media_frontend.srt_cleaning.review_clipboard import copy_review_sample
from ja_media_frontend.subsync.interaction import playback_range
from ja_media_frontend.widgets.timeline import format_clock


class SrtCleaningReviewInteractionMixin:
    """Keyboard navigation, episode switching, and playback for review."""

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        handled = True
        if event.character == "q":
            self.stop_playback()
            self.exit()
        elif event.key == "space" or event.character == " ":
            self.toggle_playback()
        elif event.character == "h":
            self.move_cue(-1)
        elif event.character == "l":
            self.move_cue(1)
        elif event.character == "j":
            self.move_source(1)
        elif event.character == "k":
            self.move_source(-1)
        elif event.character == "[":
            self.move_episode(-1)
        elif event.character == "]":
            self.move_episode(1)
        elif event.key in {"ctrl+f", "page_down", "pagedown"}:
            self.page_window(1.0)
        elif event.key in {"ctrl+b", "page_up", "pageup"}:
            self.page_window(-1.0)
        elif event.key == "ctrl+d":
            self.page_window(0.5)
        elif event.key == "ctrl+u":
            self.page_window(-0.5)
        elif event.character in {"+", "="}:
            self.zoom_window(0.5)
        elif event.character in {"-", "_"}:
            self.zoom_window(2.0)
        elif event.character == "e":
            self.action_select_episode()
        elif event.character == "c":
            self.copy_current_sample()
        else:
            handled = False
        if handled:
            event.stop()

    def move_cue(self, delta: int) -> None:
        source = self.source
        if source is None:
            return
        self.stop_playback()
        self.cue_indices[source.subtitle_id] = max(
            0,
            min(len(source.cues) - 1, self.cue_index(source) + delta),
        )
        self.ensure_cue_visible()
        self.refresh_view()

    def move_source(self, delta: int) -> None:
        sources = self.episode_sources
        if not sources:
            return
        self.stop_playback()
        self.source_index = (self.source_index + delta) % len(sources)
        self.ensure_cue_visible()
        self.refresh_view()

    def move_episode(self, delta: int) -> None:
        episodes = self.workspace.episodes or (self.episode_number,)
        if self.episode_number in episodes:
            index = episodes.index(self.episode_number)
            target = episodes[max(0, min(len(episodes) - 1, index + delta))]
        else:
            target = max(1, self.episode_number + delta)
        self.set_episode(target)

    def action_select_episode(self) -> None:
        self.push_screen(self.episode_modal(self.episode_number), self._apply_episode)

    def _apply_episode(self, episode: int | None) -> None:
        if episode is not None:
            self.set_episode(episode)

    def set_episode(self, episode: int) -> None:
        if episode == self.episode_number:
            return
        self.stop_playback()
        self.episode_number = episode
        self.source_index = 0
        self.window_start_s = 0.0
        self._load_episode_audio()
        self._prefetch_neighbors()
        self.refresh_view()

    def page_window(self, pages: float) -> None:
        self.window_start_s += pages * self.window_s
        self.normalize_window()
        self.refresh_view()

    def zoom_window(self, factor: float) -> None:
        focus = self.current_cue.start_s if self.current_cue else self.window_start_s
        self.window_s = max(5.0, min(self.timeline_end_s(), self.window_s * factor))
        self.window_start_s = focus - self.window_s / 2
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

    def normalize_window(self) -> None:
        self.window_start_s = max(
            0.0,
            min(self.timeline_end_s() - self.window_s, self.window_start_s),
        )

    def timeline_end_s(self) -> float:
        source = self.source
        return max(self.window_s, source.end_s if source is not None else 0.0)

    def toggle_playback(self) -> None:
        if self.is_playing():
            self.stop_playback()
            self._playback_status = "stopped"
            self.refresh_view()
            return
        if self._player is None:
            self._playback_status = "no audio loaded"
            self.refresh_view()
            return
        cue = self.current_cue
        if cue is None:
            return
        start_s, duration_s = playback_range(cue.original)
        self._player.play(start_s, duration_s)
        self._playback_status = f"playing {format_clock(start_s)}"
        self._stop_playback_poll()
        self._playback_poll = self.set_interval(0.1, self._on_playback_tick)
        self.refresh_view()

    def copy_current_sample(self) -> None:
        source = self.source
        cue = self.current_cue
        if source is None or cue is None:
            self._clipboard_status = "nothing to copy"
            self.refresh_view()
            return
        try:
            copy_review_sample(workspace=self.workspace, source=source, cue=cue)
        except RuntimeError as exc:
            self._clipboard_status = str(exc)
            self.notify(str(exc), severity="warning")
        else:
            self._clipboard_status = "copied review sample"
            self.notify("Copied review sample")
        self.refresh_view()

    def _load_episode_audio(self) -> None:
        loader = self._audio_loader or self._default_audio_loader
        loaded = loader(self.episode_number)
        self._audio = loaded.materialized
        self._audio_status = loaded.status
        self._player = (
            MaterializedAudioPlayer(loaded.materialized)
            if loaded.materialized is not None
            else None
        )

    def _default_audio_loader(self, episode: int) -> ReviewAudio:
        return load_review_audio(
            anilist_id=self.workspace.anilist_id,
            episode_number=episode,
            manual_audio=self.manual_audio,
            audio_profile=self.audio_profile,
        )

    def _prefetch_neighbors(self) -> None:
        if self.manual_audio is not None:
            return
        prefetch_neighbor_audio(
            anilist_id=self.workspace.anilist_id,
            episode_number=self.episode_number,
            audio_profile=self.audio_profile,
        )

    def _on_playback_tick(self) -> None:
        if not self.is_playing():
            self._stop_playback_poll()
            self.refresh_view()

    def _stop_playback_poll(self) -> None:
        if self._playback_poll is not None:
            self._playback_poll.stop()
            self._playback_poll = None

    def stop_playback(self) -> None:
        if self._player is not None:
            self._player.stop()
        self._stop_playback_poll()

    def is_playing(self) -> bool:
        return self._player is not None and self._player.is_playing()

    def playback_status(self) -> str:
        if self.is_playing():
            return self._playback_status
        if self._playback_status.startswith("playing "):
            self._playback_status = "playback finished"
        return self._playback_status
