from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from ja_media_core.audio_library import AudioStreamProbe, SourceMediaProbe
from ja_media_frontend.audio_library.discovery import (
    _run_ffprobe,
    choose_unambiguous_audio_stream,
    discover_media,
    suggest_episode_key,
)


def _stream(ordinal: int, language: str | None, *, default: bool = False) -> AudioStreamProbe:
    return AudioStreamProbe(
        global_index=ordinal + 2,
        audio_ordinal=ordinal,
        codec="flac",
        language=language,
        title=None,
        channels=2,
        sample_rate_hz=48_000,
        default=default,
    )


def test_discovery_is_immediate_supported_and_sorted(tmp_path: Path) -> None:
    (tmp_path / "B - 02.mkv").touch()
    (tmp_path / "a - 01.MP4").touch()
    (tmp_path / "notes.txt").touch()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "C - 03.mkv").touch()

    assert [path.name for path in discover_media(tmp_path)] == [
        "a - 01.MP4",
        "B - 02.mkv",
    ]


def test_episode_suggestion_rejects_multi_episode_values() -> None:
    assert (
        suggest_episode_key(
            Path("[SubsPlease] Sousou no Frieren - 03 (1080p) [ABC123].mkv")
        )
        == "3"
    )
    assert (
        suggest_episode_key(
            Path("[SubsPlease] Sousou no Frieren - 03-04 (1080p) [ABC123].mkv")
        )
        is None
    )


def test_stream_choice_prefers_one_japanese_stream() -> None:
    japanese = _stream(1, "jpn")
    probe = SourceMediaProbe(
        path=Path("episode.mkv"),
        duration_ms=1,
        size_bytes=1,
        mtime_ns=1,
        audio_streams=(_stream(0, "eng", default=True), japanese),
    )

    assert choose_unambiguous_audio_stream(probe) == japanese


def test_ffprobe_retries_signal_crashes() -> None:
    crashed = CompletedProcess(["ffprobe"], -11, "", "")
    succeeded = CompletedProcess(["ffprobe"], 0, "{}", "")

    with (
        patch(
            "ja_media_frontend.audio_library.discovery.subprocess.run",
            side_effect=(crashed, succeeded),
        ) as run,
        patch("ja_media_frontend.audio_library.discovery.time.sleep"),
    ):
        result = _run_ffprobe(["ffprobe"], Path("episode.mkv"))

    assert result.returncode == 0
    assert run.call_count == 2
    assert run.call_args_list[0].kwargs["capture_output"] is True
    assert run.call_args_list[0].kwargs["text"] is True
    assert run.call_args_list[1].args[0] == ["ffprobe"]


def test_ffprobe_reports_persistent_signal_crash() -> None:
    crashed = CompletedProcess(["ffprobe"], -11, "", "")

    with (
        patch(
            "ja_media_frontend.audio_library.discovery.subprocess.run",
            return_value=crashed,
        ),
        patch("ja_media_frontend.audio_library.discovery.time.sleep"),
    ):
        try:
            _run_ffprobe(["ffprobe"], Path("episode.mkv"), signal_retries=1)
        except RuntimeError as error:
            assert "SIGSEGV" in str(error)
            assert "2 attempts" in str(error)
        else:
            raise AssertionError("persistent signal crash should fail clearly")
