"""Backend-neutral audio coordinates shared across runtimes.

These contracts deliberately describe source media without loading or decoding
samples. Keeping them separate from :mod:`ja_media_core.audio` lets orchestration,
service, and inference-client environments exchange audio work without installing
NumPy, SoundFile, or another local decoding stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AudioSourceKind = Literal["client-local", "s3"]


@dataclass(frozen=True)
class AudioSource:
    """Stable identity and locator for source media."""

    id: str
    locator: str
    kind: AudioSourceKind = "client-local"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioFormat:
    """Observed stream/container properties in source coordinates."""

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
    """One bounded source-media interval without materialized samples."""

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
