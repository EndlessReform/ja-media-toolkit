from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np

from ja_media_core.transcripts import SubtitleCue, read_srt
from ja_media_frontend.audio import DEFAULT_PLAYBACK_SAMPLE_RATE, MaterializedAudio
from ja_media_frontend.subsync.alass import AlassRetimingError, retime_with_alass
from ja_media_frontend.subsync.service import SubtitleTrack
from ja_media_frontend.subsync.tui import SubsyncTuiApp


SRT = "1\n00:00:02,000 --> 00:00:03,000\ncandidate\n"
ANCHOR = "1\n00:00:01,000 --> 00:00:02,000\nanchor\n"


def _audio(path: Path) -> MaterializedAudio:
    return MaterializedAudio(
        source_path=path,
        sample_rate=DEFAULT_PLAYBACK_SAMPLE_RATE,
        samples=np.zeros((10 * DEFAULT_PLAYBACK_SAMPLE_RATE, 1), dtype=np.int16),
    )


def _app(tmp_path: Path) -> SubsyncTuiApp:
    media = tmp_path / "episode.mkv"
    candidate = tmp_path / "candidate.srt"
    anchor = tmp_path / "anchor.srt"
    media.write_bytes(b"")
    candidate.write_text(SRT, encoding="utf-8")
    anchor.write_text(ANCHOR, encoding="utf-8")
    return SubsyncTuiApp(
        audio_source=_audio(media),
        tracks=[SubtitleTrack(candidate, read_srt(candidate))],
        ground_truth_track=SubtitleTrack(anchor, read_srt(anchor)),
        download_dir=tmp_path / "work",
        initial_window_s=10.0,
    )


def test_retime_with_alass_uses_piecewise_p5_and_chronological_inputs(
    tmp_path: Path,
) -> None:
    seen: list[str] = []
    candidate_cues = [
        SubtitleCue(tmp_path / "source.srt", 1, 5.0, 6.0, "late"),
        SubtitleCue(tmp_path / "source.srt", 2, 2.0, 3.0, "early"),
    ]

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.extend(command)
        candidate_path = Path(command[2])
        output_path = Path(command[3])
        normalized = candidate_path.read_text(encoding="utf-8")
        assert normalized.index("early") < normalized.index("late")
        output_path.write_text(normalized, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("ja_media_frontend.subsync.alass.run_process", side_effect=fake_run):
        output = retime_with_alass(
            executable="/opt/bin/alass-cli",
            anchor_cues=candidate_cues,
            candidate_cues=candidate_cues,
            work_dir=tmp_path,
        )

    assert seen[0] == "/opt/bin/alass-cli"
    assert seen[-3:] == [
        "--disable-fps-guessing",
        "--split-penalty",
        "5",
    ]
    assert [cue.text for cue in output] == ["early", "late"]


def test_alass_absence_hides_action_and_leaves_candidate_unchanged(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[str, float]:
        app = _app(tmp_path)
        with patch(
            "ja_media_frontend.subsync.alass.find_alass_executable",
            return_value=None,
        ):
            async with app.run_test() as pilot:
                help_text = app.render_help()
                await pilot.press("a")
                return help_text, app.track.cues[0].start_s

    help_text, start_s = asyncio.run(run())

    assert "ALASS" not in help_text
    assert start_s == 2.0


def test_alass_success_retimes_only_in_memory(tmp_path: Path) -> None:
    async def run() -> tuple[float, bool, str]:
        app = _app(tmp_path)
        source_text = app.track.path.read_text(encoding="utf-8")
        shifted = [
            SubtitleCue(tmp_path / "result.srt", 1, 1.0, 2.0, "candidate")
        ]
        with (
            patch(
                "ja_media_frontend.subsync.alass.find_alass_executable",
                return_value="/opt/bin/alass-cli",
            ),
            patch(
                "ja_media_frontend.subsync.alass.retime_with_alass",
                return_value=shifted,
            ),
        ):
            async with app.run_test() as pilot:
                assert "a ALASS p5" in app.render_help()
                await pilot.press("a")
                await app.workers.wait_for_complete()
                return (
                    app.track.cues[0].start_s,
                    app.track.modified,
                    app.track.path.read_text(encoding="utf-8"),
                )
        return 0.0, False, source_text

    start_s, modified, source_text = asyncio.run(run())

    assert start_s == 1.0
    assert modified is True
    assert source_text == SRT


def test_alass_failure_leaves_candidate_unchanged(tmp_path: Path) -> None:
    async def run() -> tuple[float, bool]:
        app = _app(tmp_path)
        with (
            patch(
                "ja_media_frontend.subsync.alass.find_alass_executable",
                return_value="/opt/bin/alass-cli",
            ),
            patch(
                "ja_media_frontend.subsync.alass.retime_with_alass",
                side_effect=AlassRetimingError("broken output"),
            ),
        ):
            async with app.run_test() as pilot:
                await pilot.press("a")
                await app.workers.wait_for_complete()
                return app.track.cues[0].start_s, app.track.modified

    start_s, modified = asyncio.run(run())

    assert start_s == 2.0
    assert modified is False
