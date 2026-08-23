"""Checks for switching F5 between source and forced-aligned cue borders."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from ja_media_frontend.srt_cleaning.review_audio import ReviewAudio
from ja_media_frontend.srt_cleaning.review_loader import load_review_workspace
from ja_media_frontend.srt_cleaning.review_models import ReviewAlignment
from ja_media_frontend.srt_cleaning.review_tui import SrtCleaningReviewApp
from ja_media_frontend.widgets.timeline import TimelineWidget
from srt_cleaning_review_fixtures import prepared_run


def test_f5_toggles_timeline_and_playback_between_aligned_and_original(
    tmp_path: Path,
) -> None:
    async def run_app() -> tuple[list[tuple[float, float]], tuple[str, float, float]]:
        loaded = load_review_workspace(prepared_run(tmp_path))
        original_source = loaded.sources[0]
        aligned_cue = replace(
            original_source.cues[0],
            alignment=ReviewAlignment(5.5, 6.75, "aligned"),
        )
        source = replace(
            original_source,
            cues=(aligned_cue, *original_source.cues[1:]),
            alignment_path=tmp_path / "results.json",
        )
        workspace = replace(loaded, sources=(source, *loaded.sources[1:]))
        app = SrtCleaningReviewApp(
            workspace=workspace,
            series_label="Test Series",
            initial_episode=1,
            audio_profile="portable-aac-v1",
            manual_audio=None,
            initial_audio=ReviewAudio(None, "prepared audio"),
            audio_loader=lambda _episode: ReviewAudio(None, "prepared audio"),
        )
        player = _RecordingPlayer()
        async with app.run_test() as pilot:
            app._player = player  # type: ignore[assignment]
            await pilot.press("space")
            timeline = app.query_one("#timeline", TimelineWidget)
            assert timeline._spans[0].start_s == 5.5
            await pilot.press("t")
            await pilot.press("space")
            timeline = app.query_one("#timeline", TimelineWidget)
            return player.calls, (
                app.timing_mode,
                timeline._spans[0].start_s,
                timeline._spans[0].end_s,
            )

    calls, final_state = asyncio.run(run_app())

    assert calls == [(5.5, 1.25), (1.0, 1.0)]
    assert final_state == ("original", 1.0, 2.0)


class _RecordingPlayer:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []

    def play(self, start_s: float, duration_s: float) -> None:
        self.calls.append((start_s, duration_s))

    def stop(self) -> None:
        pass

    def is_playing(self) -> bool:
        return False
