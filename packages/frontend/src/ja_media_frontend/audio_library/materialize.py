"""ffmpeg command construction and verified atomic audio publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from ja_media_core.audio_library import (
    AnimeAudioSeriesMetadata,
    ArtifactRecord,
    AudioProfile,
    EpisodeMapping,
    SubtitleArtifactRecord,
    SubtitleStreamProbe,
)
from ja_media_core.proc import run as run_process
from ja_media_core.transcripts import read_subtitle


def artifact_filename(episode_key: str) -> str:
    """Return the canonical filename for an ordinary positive integer episode."""

    if not episode_key.isdecimal() or int(episode_key) <= 0:
        raise ValueError(
            f"Phase 1 needs an explicit filename policy for episode key {episode_key!r}"
        )
    return f"S01E{int(episode_key):03d}.m4a"


def subtitle_id(stream: SubtitleStreamProbe) -> str:
    """Return a stable per-source subtitle identifier."""

    return f"stream-{stream.global_index}"


def subtitle_filename(episode_key: str, stream: SubtitleStreamProbe) -> str:
    """Return the canonical hidden subtitle artifact filename."""

    if not episode_key.isdecimal() or int(episode_key) <= 0:
        raise ValueError(
            f"Phase 1 needs an explicit filename policy for episode key {episode_key!r}"
        )
    language = _safe_filename_part((stream.language or "und").lower()[:12])
    return f"S01E{int(episode_key):03d}.{subtitle_id(stream)}.{language}.srt"


def build_ffmpeg_command(
    mapping: EpisodeMapping,
    destination: Path,
    series: AnimeAudioSeriesMetadata,
    profile: AudioProfile,
) -> list[str]:
    """Build a shell-free ffmpeg argument vector for one episode."""

    channels = min(mapping.stream.channels or profile.max_channels, profile.max_channels)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(mapping.source_path),
        "-map",
        f"0:{mapping.stream.global_index}",
        "-vn",
        "-ac",
        str(max(channels, 1)),
        "-ar",
        str(profile.sample_rate_hz),
        "-c:a",
        profile.codec,
        "-b:a",
        str(profile.bitrate_bps),
        "-movflags",
        "+faststart",
        "-metadata",
        "language=jpn",
        "-metadata",
        f"album={series.title_preferred}",
        "-metadata",
        "album_artist=Japanese Animation",
        "-metadata",
        f"title=Episode {mapping.episode_key}",
        "-metadata",
        f"track={int(mapping.episode_key)}",
        "-metadata",
        "disc=1",
        "-metadata",
        "genre=Anime",
    ]
    if series.start_date:
        command.extend(["-metadata", f"date={series.start_date.isoformat()}"])
    command.append(str(destination))
    return command


def materialize_episode(
    mapping: EpisodeMapping,
    destination: Path,
    series: AnimeAudioSeriesMetadata,
    profile: AudioProfile,
) -> ArtifactRecord:
    """Transcode one episode, verify it, then atomically publish it."""

    temporary = destination.with_name(f".{destination.stem}.partial{destination.suffix}")
    temporary.unlink(missing_ok=True)
    try:
        run_process(
            build_ffmpeg_command(mapping, temporary, series, profile),
            check=True,
        )
        temporary_record = verify_audio_artifact(temporary, profile)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return ArtifactRecord(
        relative_path=destination.name,
        size_bytes=temporary_record.size_bytes,
        duration_ms=temporary_record.duration_ms,
        codec=temporary_record.codec,
        bitrate_bps=temporary_record.bitrate_bps,
        channels=temporary_record.channels,
        sample_rate_hz=temporary_record.sample_rate_hz,
        sha256=temporary_record.sha256,
    )


def build_subtitle_extraction_command(
    mapping: EpisodeMapping,
    stream: SubtitleStreamProbe,
    destination: Path,
) -> list[str]:
    """Build a shell-free ffmpeg command to normalize one subtitle stream to SRT."""

    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(mapping.source_path),
        "-map",
        f"0:{stream.global_index}",
        "-c:s",
        "subrip",
        str(destination),
    ]


def materialize_subtitle(
    mapping: EpisodeMapping,
    stream: SubtitleStreamProbe,
    destination: Path,
    *,
    relative_path: str,
) -> SubtitleArtifactRecord:
    """Extract one subtitle stream, verify it, and atomically publish it."""

    temporary = destination.with_name(f".{destination.stem}.partial{destination.suffix}")
    temporary.unlink(missing_ok=True)
    try:
        run_process(
            build_subtitle_extraction_command(mapping, stream, temporary),
            check=True,
        )
        verify_subtitle_artifact(temporary)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return subtitle_artifact_record(stream, destination, relative_path=relative_path)


def verify_subtitle_artifact(path: Path) -> None:
    """Verify one extracted subtitle artifact is parseable and non-empty."""

    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"subtitle artifact is empty or missing: {path}")
    if not read_subtitle(path):
        raise ValueError(f"subtitle artifact has no cues: {path}")


def subtitle_artifact_record(
    stream: SubtitleStreamProbe,
    path: Path,
    *,
    relative_path: str,
) -> SubtitleArtifactRecord:
    """Build a manifest record for an already verified subtitle file."""

    return SubtitleArtifactRecord(
        subtitle_id=subtitle_id(stream),
        relative_path=relative_path,
        size_bytes=path.stat().st_size,
        codec=stream.codec,
        language=stream.language,
        title=stream.title,
        default=stream.default,
        source_stream_index=stream.global_index,
        source_stream_ordinal=stream.subtitle_ordinal,
        sha256=_sha256(path),
    )


def verify_audio_artifact(path: Path, profile: AudioProfile) -> ArtifactRecord:
    """Verify one nonempty audio artifact against the selected profile."""

    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"audio artifact is empty or missing: {path}")
    result = run_process(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=duration,bit_rate:stream=codec_name,channels,sample_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    if len(streams) != 1:
        raise ValueError(f"expected one audio stream in {path}")
    stream = streams[0]
    codec = str(stream.get("codec_name") or "")
    channels = int(stream.get("channels") or 0)
    sample_rate = int(stream.get("sample_rate") or 0)
    duration_ms = round(float((payload.get("format") or {}).get("duration") or 0) * 1000)
    if codec != profile.codec:
        raise ValueError(f"expected codec {profile.codec}, found {codec}")
    if channels <= 0 or channels > profile.max_channels:
        raise ValueError(f"invalid channel count {channels}")
    if sample_rate != profile.sample_rate_hz:
        raise ValueError(f"expected {profile.sample_rate_hz} Hz, found {sample_rate}")
    if duration_ms <= 0:
        raise ValueError("artifact duration is zero")
    return ArtifactRecord(
        relative_path=path.name,
        size_bytes=path.stat().st_size,
        duration_ms=duration_ms,
        codec=codec,
        bitrate_bps=_optional_int((payload.get("format") or {}).get("bit_rate")),
        channels=channels,
        sample_rate_hz=sample_rate,
        sha256=_sha256(path),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return cleaned or "und"
