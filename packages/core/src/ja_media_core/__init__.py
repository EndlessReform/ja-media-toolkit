from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


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
