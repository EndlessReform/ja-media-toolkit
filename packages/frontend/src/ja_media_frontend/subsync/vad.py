"""VAD tuning and display helpers for the subsync TUI."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rich.panel import Panel
from rich.text import Text

from ja_media_core.audio import full_audio_chunk, probe_audio_source, resolve_audio_source
from ja_media_core.transcripts import SubtitleCue
from ja_media_core.vad import (
    SpeechSpan,
    VadBackend,
    VadOptions,
    VadTimeline,
)
from ja_media_core.vad_predictions import (
    VadPredictionTimeline,
    speech_timeline_from_predictions,
)
from ja_media_frontend.subsync.vad_dialogs import VadThresholdModal
from ja_media_frontend.widgets.timeline import TimedSpan, format_clock


DEFAULT_TUI_VAD_THRESHOLD = 0.5


class SubsyncVadMixin:
    """Speech-track state and threshold tuning for the subtitle review TUI."""

    def init_vad(
        self,
        vad_backend: VadBackend | None,
        *,
        vad_options: VadOptions | None = None,
        vad_audio_path: Path | None = None,
    ) -> None:
        self.vad_backend = vad_backend
        self.vad_options_base = vad_options or VadOptions()
        self.vad_audio_path = vad_audio_path
        self.vad_threshold = (
            self.vad_options_base.threshold
            if self.vad_options_base.threshold is not None
            else DEFAULT_TUI_VAD_THRESHOLD
        )
        self.vad_timeline: VadTimeline | None = None
        self.vad_prediction_timeline: VadPredictionTimeline | None = None
        self.vad_timeline_cache: dict[tuple[Any, ...], VadTimeline] = {}
        self.vad_prediction_attempted = False
        self.vad_cache_key: str | None = None
        self.cue_mode = "srt"
        self.vad_cue_index = 0

    def on_vad_mount(self) -> None:
        if self.vad_backend is None:
            self.notify("VAD backend not available", severity="warning")
            return
        self.run_worker(self._process_vad_worker)

    async def _process_vad_worker(self) -> None:
        if self.vad_backend is None:
            return

        threshold = self.vad_threshold
        options = self._vad_options(threshold)
        if self._restore_cached_vad_timeline(options):
            return

        self.notify(f"Processing VAD at threshold {threshold:.2f}...")
        try:
            timeline = await asyncio.to_thread(self._build_vad_timeline, options)
        except Exception as exc:
            self.notify(f"VAD processing failed: {exc}", severity="error")
            return

        self.vad_timeline = timeline
        self.vad_timeline_cache[self._vad_cache_key(options)] = timeline
        self.vad_cache_key = str(self.audio_source.source_path)
        self._clamp_vad_cue_index()
        self.refresh_view()
        self.notify(f"VAD ready at threshold {threshold:.2f}")

    def _restore_cached_vad_timeline(self, options: VadOptions) -> bool:
        if self.vad_prediction_timeline is not None:
            self.vad_timeline = speech_timeline_from_predictions(
                self.vad_prediction_timeline,
                options=options,
            )
            self.vad_timeline_cache[self._vad_cache_key(options)] = self.vad_timeline
            self._clamp_vad_cue_index()
            self.refresh_view()
            return True

        cached = self.vad_timeline_cache.get(self._vad_cache_key(options))
        if cached is None:
            return False
        self.vad_timeline = cached
        self._clamp_vad_cue_index()
        self.refresh_view()
        return True

    def _build_vad_timeline(self, options: VadOptions) -> VadTimeline:
        chunk = self._source_audio_chunk()
        if not self.vad_prediction_attempted:
            self.vad_prediction_attempted = True
            try:
                predictor = self.vad_backend
                predictions = predictor.predict(  # type: ignore[attr-defined]
                    [chunk],
                    options=options,
                )[0]
            except (AttributeError, TypeError):
                predictions = None
            if predictions is not None:
                self.vad_prediction_timeline = predictions
                return speech_timeline_from_predictions(
                    predictions,
                    options=options,
                )

        return self.vad_backend.detect([chunk], options=options)[0]

    def _source_audio_chunk(self) -> Any:
        source_path = self.vad_audio_path or self.audio_source.source_path
        source = resolve_audio_source(source_path)
        audio_format = probe_audio_source(source)
        return full_audio_chunk(source, audio_format)

    def _vad_options(self, threshold: float) -> VadOptions:
        return VadOptions(
            threshold=threshold,
            min_speech_s=self.vad_options_base.min_speech_s,
            min_silence_s=self.vad_options_base.min_silence_s,
            speech_pad_s=self.vad_options_base.speech_pad_s,
            merge_gap_s=self.vad_options_base.merge_gap_s,
            channel=self.vad_options_base.channel,
            metadata=dict(self.vad_options_base.metadata),
        )

    def _vad_cache_key(self, options: VadOptions) -> tuple[Any, ...]:
        return (
            options.threshold,
            options.min_speech_s,
            options.min_silence_s,
            options.speech_pad_s,
            options.merge_gap_s,
            options.channel,
        )

    def action_set_cue_mode_vad(self) -> None:
        """Set cue navigation to use VAD speech spans."""

        if self.cue_mode == "vad":
            return
        focus_time = self._focus_time_s()
        self.cue_mode = "vad"
        self.select_cue_near_time(focus_time)
        self.refresh_view()

    def action_set_cue_mode_srt(self) -> None:
        """Set cue navigation to use SRT subtitles."""

        if self.cue_mode == "srt":
            return
        focus_time = self._focus_time_s()
        self.cue_mode = "srt"
        self.select_cue_near_time(focus_time)
        self.refresh_view()

    def action_open_vad_threshold(self) -> None:
        self.push_screen(
            VadThresholdModal(self.vad_threshold),
            self.apply_vad_threshold,
        )

    def apply_vad_threshold(self, threshold: float | None) -> None:
        if threshold is None:
            return
        if threshold == self.vad_threshold:
            return
        focus_time = self._focus_time_s()
        self.vad_threshold = threshold
        self.cue_mode = "vad"
        if self._restore_cached_vad_timeline(self._vad_options(threshold)):
            self.select_cue_near_time(focus_time)
            self.refresh_view()
            return
        self.run_worker(self._process_vad_worker)

    def timeline_layers(self) -> dict[str, tuple[TimedSpan, ...]]:
        return {
            f"Speech {self.vad_threshold:.2f}": tuple(
                self.vad_timeline.speech if self.vad_timeline else ()
            ),
            "Subtitle": tuple(self.track.cues if self.tracks else ()),
        }

    def active_timeline_layer(self) -> str:
        return f"Speech {self.vad_threshold:.2f}" if self.cue_mode == "vad" else "Subtitle"

    def vad_timeline_end_s(self) -> float | None:
        if self.vad_timeline is None:
            return None
        return self.vad_timeline.chunk.end_s

    def render_active_cue(self) -> Panel:
        if not self.tracks and self.cue_mode == "srt":
            return Panel("No subtitle candidates are loaded.", title="Current subtitle")
        cue = self.current_cue
        if cue is None:
            return Panel("No cues in selected source.", title="Current cue")

        title = "Current subtitle" if self.cue_mode == "srt" else "Current speech (VAD)"
        text = Text()
        text.append(
            f"{self.cue_index + 1}  {format_clock(cue.start_s)} -> {format_clock(cue.end_s)}",
            style="bold cyan",
        )
        if self.is_playing():
            text.append(" \u25b6", style="bold orange3")
        text.append("\n")

        cue_text = self._active_cue_text(cue)
        text.append(cue_text or "<empty cue>", style=None if self.cue_mode == "srt" else "dim")
        return Panel(text, title=title, expand=True)

    def _active_cue_text(self, cue: SubtitleCue | SpeechSpan) -> str:
        if self.cue_mode == "srt":
            return getattr(cue, "text", "")
        overlapping = self._find_overlapping_srt_cue(cue)
        if overlapping is None:
            return "No overlapping subtitle"
        return overlapping.text

    def _find_overlapping_srt_cue(
        self,
        vad_cue: SubtitleCue | SpeechSpan,
    ) -> SubtitleCue | None:
        if not self.tracks or not self.track.cues:
            return None

        best_cue = None
        max_overlap = 0.0
        for cue in self.track.cues:
            overlap = min(cue.end_s, vad_cue.end_s) - max(cue.start_s, vad_cue.start_s)
            if overlap > max_overlap:
                max_overlap = overlap
                best_cue = cue
        return best_cue

    def _clamp_vad_cue_index(self) -> None:
        if self.vad_timeline is None or not self.vad_timeline.speech:
            self.vad_cue_index = 0
            return
        self.vad_cue_index = max(
            0,
            min(self.vad_cue_index, len(self.vad_timeline.speech) - 1),
        )
