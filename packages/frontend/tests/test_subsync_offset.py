from __future__ import annotations

import asyncio
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from ja_media_core.transcripts import read_srt
from ja_media_frontend.audio import (
    DEFAULT_PLAYBACK_SAMPLE_RATE,
    MaterializedAudio,
)
from ja_media_frontend.subsync.service import SubtitleTrack
from ja_media_frontend.subsync.tui import SubsyncTuiApp
from rich.console import Console


SRT_TEXT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "hello\n\n"
)


def make_audio_source(path: Path) -> MaterializedAudio:
    frame_count = round(10.0 * DEFAULT_PLAYBACK_SAMPLE_RATE)
    return MaterializedAudio(
        source_path=path,
        sample_rate=DEFAULT_PLAYBACK_SAMPLE_RATE,
        samples=np.zeros((frame_count, 1), dtype=np.int16),
    )


class SubsyncOffsetTest(unittest.TestCase):
    def test_z_and_x_expose_cumulative_offset_in_candidates(self) -> None:
        async def run_app() -> tuple[str, str]:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mkv"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"")
                srt.write_text(SRT_TEXT, encoding="utf-8")

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                async with app.run_test() as pilot:
                    await pilot.press("z", "z", "x")
                    output = io.StringIO()
                    console = Console(file=output, width=100)
                    console.print(app.render_candidates())
                    return app.track.timing_offset_label, output.getvalue()

        label, candidates = asyncio.run(run_app())

        self.assertEqual(label, "-100ms")
        self.assertIn("-100ms", candidates)


if __name__ == "__main__":
    unittest.main()
