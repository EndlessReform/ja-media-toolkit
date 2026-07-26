from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from ja_media_core.transcripts import parse_srt, read_srt
from ja_media_frontend.audio import DEFAULT_PLAYBACK_SAMPLE_RATE, MaterializedAudio
from ja_media_frontend.subsync.candidates import anchor_fit_label
from ja_media_frontend.subsync.service import SubtitleTrack
from ja_media_frontend.subsync.tui import SubsyncTuiApp


CANDIDATE_SRT = (
    "1\n"
    "00:00:02,000 --> 00:00:03,000\n"
    "candidate\n"
)

TRUTH_SRT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "truth text should stay hidden\n"
)


def _audio(path: Path) -> MaterializedAudio:
    samples = np.zeros((10 * DEFAULT_PLAYBACK_SAMPLE_RATE, 1), dtype=np.int16)
    return MaterializedAudio(
        source_path=path,
        sample_rate=DEFAULT_PLAYBACK_SAMPLE_RATE,
        samples=samples,
    )


def test_ground_truth_is_rendered_but_not_selectable_or_shifted(tmp_path: Path) -> None:
    async def run_app() -> tuple[int, str, float, float, str]:
        media = tmp_path / "episode.mkv"
        candidate = tmp_path / "candidate.srt"
        truth = tmp_path / "truth.srt"
        media.write_bytes(b"fake")
        candidate.write_text(CANDIDATE_SRT, encoding="utf-8")
        truth.write_text(TRUTH_SRT, encoding="utf-8")

        ground_truth = SubtitleTrack(
            truth,
            read_srt(truth),
            repo_path="embedded/eng",
            subtitle_id="stream-2",
        )
        app = SubsyncTuiApp(
            audio_source=_audio(media),
            tracks=[SubtitleTrack(candidate, read_srt(candidate))],
            ground_truth_track=ground_truth,
            ground_truth_status="truth eng",
            initial_window_s=10.0,
        )
        async with app.run_test() as pilot:
            await pilot.press("j")
            await pilot.press("x")
            candidate_start = app.track.cues[0].start_s
            truth_start = app.ground_truth_track.cues[0].start_s
            return (
                len(app.render_candidates().rows),
                app.render_active_cue().renderable.plain,
                candidate_start,
                truth_start,
                app.render_candidates().columns[4].header,
            )

    row_count, active_text, candidate_start, truth_start, header = asyncio.run(run_app())

    assert row_count == 2
    assert header == "fit"
    assert "candidate" in active_text
    assert "truth text should stay hidden" not in active_text
    assert candidate_start == pytest.approx(2.1)
    assert truth_start == 1.0


def test_ground_truth_sorts_candidates_by_timing_fit(tmp_path: Path) -> None:
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")
    truth = SubtitleTrack(tmp_path / "truth.srt", read_srt_from_text(TRUTH_SRT))
    poor = SubtitleTrack(
        tmp_path / "poor.srt",
        read_srt_from_text(
            "1\n00:00:08,000 --> 00:00:09,000\npoor fit\n"
        ),
    )
    good = SubtitleTrack(
        tmp_path / "good.srt",
        read_srt_from_text(
            "1\n00:00:01,000 --> 00:00:02,000\ngood fit\n"
        ),
    )
    app = SubsyncTuiApp(
        audio_source=_audio(media),
        tracks=[poor, good],
        ground_truth_track=truth,
        initial_window_s=10.0,
    )

    app.sort_tracks_by_language()

    assert app.tracks == [good, poor]
    assert app.track is good
    assert anchor_fit_label(truth, good) == "1.00"
    assert anchor_fit_label(truth, poor) == "0.00"


def read_srt_from_text(text: str):
    return parse_srt(text)
