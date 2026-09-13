"""Subtitle export works with identity-only playback and edited cues."""
import asyncio
from pathlib import Path

import numpy as np
import pytest
from textual.widgets import Input

from ja_media_core.transcripts import read_srt
from ja_media_frontend.audio import MaterializedAudio
from ja_media_frontend.subsync.service import SubtitleTrack
from ja_media_frontend.subsync.subtitle_export import save_subtitle
from ja_media_frontend.subsync.tui import SubsyncTuiApp

SRT = "1\n00:00:01,000 --> 00:00:02,000\nhello\n"


def test_save_key_without_promotion_target(tmp_path: Path) -> None:
    source = tmp_path / "candidate.srt"
    source.write_text(SRT)
    destination = tmp_path / "saved.srt"
    app = SubsyncTuiApp(
        audio_source=MaterializedAudio(
            source_path=tmp_path / "cached.m4a", sample_rate=48000,
            samples=np.zeros((48000, 1), dtype=np.int16),
        ),
        tracks=[SubtitleTrack(path=source, cues=read_srt(source))],
        initial_window_s=10, promotion_target=None,
    )

    async def exercise() -> None:
        async with app.run_test() as pilot:
            app.shift_track_timing(0.1)
            await pilot.press("S")
            app.screen.query_one("#save-path", Input).value = str(destination)
            await pilot.click("#save-confirm")
            await pilot.pause()

    asyncio.run(exercise())
    assert read_srt(destination)[0].start_s == pytest.approx(1.1)
    assert source.read_text() == SRT


def test_export_requires_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "candidate.srt"
    source.write_text(SRT)
    track = SubtitleTrack(path=source, cues=read_srt(source))
    destination = tmp_path / "saved.srt"
    destination.write_text("keep")
    with pytest.raises(FileExistsError):
        save_subtitle(track, destination)
    assert destination.read_text() == "keep"
    save_subtitle(track, destination, overwrite=True)
    assert destination.read_text() == SRT
