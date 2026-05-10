from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ja_media_core.audio import (
    AudioChunk,
    AudioFormat,
    AudioSource,
    InMemoryAudioChunk,
    full_audio_chunk,
    materialize_audio_chunk,
    probe_audio_source,
    resolve_audio_source,
    write_audio_chunk,
)
from ja_media_core.vad import (
    SpeechSpan,
    VadBackend,
    VadOptions,
    VadTimeline,
    normalize_speech_spans,
    plan_vad_splits,
    speech_chunks_from_timeline,
    speech_chunks_from_timelines,
    validate_speech_spans,
)


@dataclass(frozen=True)
class TranscriptionJob:
    input_path: Path
    language: str = "ja"
    model: str | None = None


@dataclass(frozen=True)
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class Transcript:
    segments: list[TranscriptSegment]


class Transcriber(Protocol):
    def transcribe(self, job: TranscriptionJob) -> Transcript: ...


__all__ = [
    "AudioChunk",
    "AudioFormat",
    "AudioSource",
    "InMemoryAudioChunk",
    "SpeechSpan",
    "Transcriber",
    "TranscriptionJob",
    "Transcript",
    "TranscriptSegment",
    "VadBackend",
    "VadOptions",
    "VadTimeline",
    "full_audio_chunk",
    "materialize_audio_chunk",
    "normalize_speech_spans",
    "plan_vad_splits",
    "probe_audio_source",
    "resolve_audio_source",
    "speech_chunks_from_timeline",
    "speech_chunks_from_timelines",
    "validate_speech_spans",
    "write_audio_chunk",
]
