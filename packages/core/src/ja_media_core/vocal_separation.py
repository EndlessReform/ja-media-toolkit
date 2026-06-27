from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from ja_media_core.audio import AudioChunk


@dataclass(frozen=True)
class VocalSeparationOptions:
    """Runtime policy for producing a vocal stem from source audio.

    This object deliberately stops at artifact production.  Callers such as the
    subsync TUI or transcribe command decide whether the resulting stem should
    feed VAD, ASR, playback, or some future analysis step.
    """

    stem: str = "vocals"
    cache_dir: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VocalSeparationResult:
    """One separated stem plus provenance back to the source chunk."""

    source_chunk: AudioChunk
    stem_chunk: AudioChunk
    backend: str
    cache_hit: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class VocalSeparationBackend(Protocol):
    """Backend interface for source-separation runtimes such as Demucs or UVR."""

    name: str

    def separate(
        self,
        chunks: Sequence[AudioChunk],
        *,
        options: VocalSeparationOptions | None = None,
    ) -> list[VocalSeparationResult]:
        ...
