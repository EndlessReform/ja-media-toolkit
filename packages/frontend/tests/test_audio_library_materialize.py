from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from ja_media_core.audio_library import (
    AnimeAudioSeriesMetadata,
    AudioStreamProbe,
    EpisodeMapping,
    PORTABLE_AAC_V1,
    SourceMediaProbe,
)
from ja_media_frontend.audio_library.materialize import (
    artifact_filename,
    build_ffmpeg_command,
    materialize_episode,
)


def _series() -> AnimeAudioSeriesMetadata:
    return AnimeAudioSeriesMetadata(
        anilist_id=1,
        title_english="Example",
        title_native="例",
        title_romaji=None,
        title_preferred="Example",
        description_html=None,
        description_text=None,
        format="TV",
        status="FINISHED",
        season=None,
        season_year=2024,
        episode_count=1,
        typical_duration_minutes=24,
        start_date=date(2024, 1, 2),
        end_date=None,
        genres=(),
        source=None,
        country_of_origin="JP",
        cover_url=None,
        banner_url=None,
        mal_id=None,
        site_url=None,
        upstream_updated_at=None,
        raw_snapshot={},
    )


def test_ffmpeg_uses_global_stream_index_and_preserves_mono() -> None:
    stream = AudioStreamProbe(4, 1, "flac", "jpn", None, 1, 48_000, True)
    source = SourceMediaProbe(Path("/source/ep.mkv"), 1000, 2, 3, (stream,))
    mapping = EpisodeMapping("7", source, stream)

    command = build_ffmpeg_command(
        mapping, Path("/output/.S01E007.partial.m4a"), _series(), PORTABLE_AAC_V1
    )

    assert command[command.index("-map") + 1] == "0:4"
    assert command[command.index("-ac") + 1] == "1"
    assert "track=7" in command
    assert command[-1] == "/output/.S01E007.partial.m4a"


def test_artifact_filename_requires_ordinary_episode_key() -> None:
    assert artifact_filename("12") == "S01E012.m4a"

    for unsupported in ("0", "12.5", "SP1"):
        try:
            artifact_filename(unsupported)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{unsupported} should require a future filename policy")


def test_materialize_episode_round_trip_with_ffmpeg(tmp_path: Path) -> None:
    source_path = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.25",
            str(source_path),
        ],
        check=True,
    )
    stream = AudioStreamProbe(0, 0, "pcm_s16le", None, None, 1, 44_100, True)
    source = SourceMediaProbe(
        source_path,
        duration_ms=250,
        size_bytes=source_path.stat().st_size,
        mtime_ns=source_path.stat().st_mtime_ns,
        audio_streams=(stream,),
    )
    destination = tmp_path / "S01E001.m4a"

    artifact = materialize_episode(
        EpisodeMapping("1", source, stream),
        destination,
        _series(),
        PORTABLE_AAC_V1,
    )

    assert destination.is_file()
    assert artifact.codec == "aac"
    assert artifact.channels == 1
    assert artifact.sample_rate_hz == 48_000
    assert artifact.sha256
