from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import numpy as np
import soundfile as sf
from numpy.typing import NDArray


AudioSourceKind = Literal["client-local", "s3"]


@dataclass(frozen=True)
class AudioSource:
    id: str
    locator: str
    kind: AudioSourceKind = "client-local"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioFormat:
    sample_rate_hz: int
    channels: int
    duration_s: float | None = None
    codec: str | None = None
    container: str | None = None
    frame_count: int | None = None
    sample_width_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioChunk:
    source: AudioSource
    start_s: float
    end_s: float
    source_start_frame: int | None = None
    source_end_frame: int | None = None
    format: AudioFormat | None = None
    kind: str = "media_fragment"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class InMemoryAudioChunk:
    chunk: AudioChunk
    samples: NDArray[np.float32]
    sample_rate_hz: int
    source_sample_rate_hz: int
    channels: int
    normalized: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def frame_count(self) -> int:
        return int(self.samples.shape[0])

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.sample_rate_hz


def resolve_audio_source(
    locator: str | Path,
    *,
    base_dir: str | Path | None = None,
    source_id: str | None = None,
    must_exist: bool = False,
) -> AudioSource:
    raw_locator = str(locator)
    parsed = urlparse(raw_locator)

    if parsed.scheme == "s3":
        if not parsed.netloc or not parsed.path.strip("/"):
            raise ValueError(f"Invalid S3 audio locator: {raw_locator!r}")
        return AudioSource(
            id=source_id or _source_id_from_s3(parsed.netloc, parsed.path),
            locator=raw_locator,
            kind="s3",
            metadata={
                "bucket": parsed.netloc,
                "key": parsed.path.lstrip("/"),
                "input_locator": raw_locator,
            },
        )

    if parsed.scheme:
        raise ValueError(f"Unsupported audio locator scheme: {parsed.scheme!r}")

    base = Path.cwd() if base_dir is None else Path(base_dir)
    path = Path(raw_locator).expanduser()
    if not path.is_absolute():
        path = base / path
    resolved = path.resolve(strict=must_exist)

    return AudioSource(
        id=source_id or resolved.stem,
        locator=str(resolved),
        kind="client-local",
        metadata={"input_locator": raw_locator},
    )


def probe_audio_source(source: AudioSource) -> AudioFormat:
    path = _require_client_local_path(source)
    try:
        info = sf.info(str(path))
    except sf.LibsndfileError:
        return _probe_audio_source_with_ffprobe(path)

    return AudioFormat(
        sample_rate_hz=info.samplerate,
        channels=info.channels,
        duration_s=info.duration,
        codec=info.subtype.lower() if info.subtype else None,
        container=info.format.lower() if info.format else None,
        frame_count=info.frames,
        metadata={
            "format_info": info.format_info,
            "subtype_info": info.subtype_info,
        },
    )


def full_audio_chunk(
    source: AudioSource,
    audio_format: AudioFormat,
    *,
    kind: str = "source",
    metadata: dict[str, Any] | None = None,
) -> AudioChunk:
    if audio_format.duration_s is None:
        raise ValueError("Audio format must include duration_s to build a full chunk")
    return AudioChunk(
        source=source,
        start_s=0.0,
        end_s=audio_format.duration_s,
        source_start_frame=0,
        source_end_frame=audio_format.frame_count,
        format=audio_format,
        kind=kind,
        metadata={} if metadata is None else dict(metadata),
    )


def materialize_audio_chunk(chunk: AudioChunk) -> InMemoryAudioChunk:
    source_format = chunk.format or probe_audio_source(chunk.source)
    path = _require_client_local_path(chunk.source)
    start_frame = _frame_index(
        chunk.start_s,
        source_format.sample_rate_hz,
        explicit_frame=chunk.source_start_frame,
    )
    end_frame = _frame_index(
        chunk.end_s,
        source_format.sample_rate_hz,
        explicit_frame=chunk.source_end_frame,
    )
    if end_frame < start_frame:
        raise ValueError("Audio chunk end must not be before start")
    if source_format.frame_count is not None and end_frame > source_format.frame_count:
        raise ValueError("Audio chunk end is beyond the source frame count")

    try:
        samples, sample_rate_hz = sf.read(
            str(path),
            start=start_frame,
            stop=end_frame,
            dtype="float32",
            always_2d=True,
        )
    except sf.LibsndfileError:
        samples, sample_rate_hz = _decode_audio_chunk_with_ffmpeg(
            path,
            source_format=source_format,
            start_s=chunk.start_s,
            end_s=chunk.end_s,
        )
    if sample_rate_hz != source_format.sample_rate_hz:
        raise ValueError("Audio sample rate changed between probe and decode")
    if samples.shape[1] != source_format.channels:
        raise ValueError("Audio channel count changed between probe and decode")

    materialized_chunk = AudioChunk(
        source=chunk.source,
        start_s=start_frame / source_format.sample_rate_hz,
        end_s=end_frame / source_format.sample_rate_hz,
        source_start_frame=start_frame,
        source_end_frame=end_frame,
        format=source_format,
        kind=chunk.kind,
        metadata=dict(chunk.metadata),
    )

    return InMemoryAudioChunk(
        chunk=materialized_chunk,
        samples=samples,
        sample_rate_hz=source_format.sample_rate_hz,
        source_sample_rate_hz=source_format.sample_rate_hz,
        channels=source_format.channels,
        normalized=True,
        metadata={"dtype": "float32", "shape": tuple(samples.shape)},
    )


def write_audio_chunk(
    chunk: AudioChunk,
    output_path: str | Path,
    *,
    format: str | None = None,
    subtype: str | None = "PCM_16",
) -> Path:
    materialized = materialize_audio_chunk(chunk)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(
        str(path),
        materialized.samples,
        materialized.sample_rate_hz,
        format=format,
        subtype=subtype,
    )
    return path


def _require_client_local_path(source: AudioSource) -> Path:
    if source.kind != "client-local":
        raise ValueError(f"Audio source is not client-local: {source.kind!r}")
    return Path(source.locator)


def _probe_audio_source_with_ffprobe(path: Path) -> AudioFormat:
    """Probe containers such as M4A and Opus through ffprobe.

    ``soundfile`` is still the first choice for PCM-oriented formats because it
    gives exact frame counts. ffprobe is the ingestion fallback for common
    compressed media that ffmpeg can decode but libsndfile may not recognize.
    """

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError(
            f"Could not probe {path}: libsndfile does not support it and ffprobe "
            "is not installed"
        )

    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            (
                "stream=codec_name,sample_rate,channels,duration"
                ":format=format_name,duration"
            ),
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise ValueError(f"No audio stream found in {path}")

    stream = streams[0]
    sample_rate_hz = int(stream["sample_rate"])
    channels = int(stream["channels"])
    duration_s = _optional_float(stream.get("duration"))
    if duration_s is None:
        duration_s = _optional_float((payload.get("format") or {}).get("duration"))

    frame_count = None if duration_s is None else round(duration_s * sample_rate_hz)

    format_name = (payload.get("format") or {}).get("format_name")
    return AudioFormat(
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        duration_s=duration_s,
        codec=stream.get("codec_name"),
        container=_container_name(path, format_name),
        frame_count=frame_count,
        metadata={
            "decoder": "ffprobe",
            "format_name": format_name,
        },
    )


def _decode_audio_chunk_with_ffmpeg(
    path: Path,
    *,
    source_format: AudioFormat,
    start_s: float,
    end_s: float,
) -> tuple[NDArray[np.float32], int]:
    """Decode an audio span to float32 frames using ffmpeg."""

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            f"Could not decode {path}: libsndfile does not support it and ffmpeg "
            "is not installed"
        )

    duration_s = end_s - start_s
    if duration_s < 0:
        raise ValueError("Audio chunk end must not be before start")

    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start_s:.9f}",
            "-t",
            f"{duration_s:.9f}",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            str(source_format.channels),
            "-ar",
            str(source_format.sample_rate_hz),
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    samples = np.frombuffer(completed.stdout, dtype="<f4")
    if samples.size % source_format.channels != 0:
        raise ValueError("Decoded audio sample count is not divisible by channels")
    return samples.reshape((-1, source_format.channels)).astype(np.float32), (
        source_format.sample_rate_hz
    )


def _source_id_from_s3(bucket: str, path: str) -> str:
    key = path.strip("/")
    stem = Path(key).stem
    return stem or bucket


def _frame_index(
    timestamp_s: float,
    sample_rate_hz: int,
    *,
    explicit_frame: int | None = None,
) -> int:
    if explicit_frame is not None:
        return explicit_frame
    return round(timestamp_s * sample_rate_hz)


def _optional_float(value: Any) -> float | None:
    if value in (None, "N/A"):
        return None
    return float(value)


def _container_name(path: Path, format_name: str | None) -> str | None:
    suffix = path.suffix.lower().removeprefix(".")
    if suffix and format_name and suffix in format_name.split(","):
        return suffix
    return suffix or format_name
