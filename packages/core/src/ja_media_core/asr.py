from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Sequence

from ja_media_core.audio import AudioChunk


AsrTask = Literal["transcribe", "translate"]
AsrJobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


@dataclass(frozen=True)
class AsrRequest:
    """Backend-neutral description of audio transcription intent.

    This is the stable data-transfer object for manifests, queues, and local
    calls. It deliberately contains user intent rather than model sampling
    controls: language, task, contextual hints, and which source-coordinate
    chunks should be transcribed.
    """

    chunks: tuple[AudioChunk, ...]
    language: str | None = "ja"
    task: AsrTask = "transcribe"
    context: str | None = None
    hotwords: tuple[str, ...] = ()
    timestamps: bool = True
    diarization: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.chunks:
            raise ValueError("ASR requests must include at least one audio chunk")


@dataclass(frozen=True)
class AsrRuntimeOptions:
    """Invocation-time controls that do not belong in the durable request DTO.

    Backends may interpret ``backend_options`` as needed. For example, a vLLM
    backend might accept ``temperature`` or ``max_tokens`` while a Whisper-style
    backend might accept ``beam_size``. Core keeps these opaque because model
    runtimes are intentionally replaceable.
    """

    timeout_s: float | None = None
    backend_options: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AsrJob:
    """Queue-friendly wrapper around a request and its submission policy.

    Use this when work needs to cross a process or machine boundary. Synchronous
    callers can pass ``AsrRequest`` directly to an ``AsrBackend`` instead.
    """

    id: str
    request: AsrRequest
    backend: str | None = None
    runtime_options: AsrRuntimeOptions | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AsrJobRecord:
    """Observable state for submitted ASR work.

    The record is intentionally small so services can persist it without
    coupling to one backend's scheduler, retry, or transport details.
    """

    job: AsrJob
    status: AsrJobStatus
    transcript_refs: tuple[str, ...] = ()
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AsrSegment:
    """One transcript span in source-relative time."""

    chunk: AudioChunk
    start_s: float
    end_s: float
    text: str
    speaker: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end_s <= self.start_s:
            raise ValueError("ASR segments must have positive duration")
        if self.start_s < self.chunk.start_s:
            raise ValueError("ASR segment starts before the source chunk")
        if self.end_s > self.chunk.end_s:
            raise ValueError("ASR segment ends after the source chunk")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("ASR confidence must be between 0.0 and 1.0")


@dataclass(frozen=True)
class AsrTranscript:
    """Backend-normalized transcription result for one source chunk."""

    chunk: AudioChunk
    text: str
    segments: tuple[AsrSegment, ...]
    backend: str
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for segment in self.segments:
            if segment.chunk != self.chunk:
                raise ValueError("ASR transcript segments must reference transcript chunk")


class AsrBackend(Protocol):
    """Synchronous ASR backend over core request/result contracts."""

    name: str

    def transcribe(
        self,
        request: AsrRequest,
        *,
        runtime_options: AsrRuntimeOptions | None = None,
    ) -> list[AsrTranscript]:
        ...


class AsyncAsrBackend(Protocol):
    """Async ASR backend for transports that benefit from concurrent submit.

    This is the preferred boundary for remote decode-heavy backends such as
    vLLM: callers can keep many chunk requests in flight while preserving the
    same durable ``AsrRequest`` and ``AsrTranscript`` contracts.
    """

    name: str

    async def transcribe_async(
        self,
        request: AsrRequest,
        *,
        runtime_options: AsrRuntimeOptions | None = None,
    ) -> list[AsrTranscript]:
        ...


class AsrJobSubmitter(Protocol):
    """Asynchronous or remote ASR job submission boundary."""

    name: str

    def submit(self, job: AsrJob) -> AsrJobRecord:
        ...

    def get(self, job_id: str) -> AsrJobRecord:
        ...


def asr_request_from_chunks(
    chunks: Sequence[AudioChunk],
    *,
    language: str | None = "ja",
    task: AsrTask = "transcribe",
    context: str | None = None,
    hotwords: Sequence[str] = (),
    timestamps: bool = True,
    diarization: bool = False,
    metadata: dict[str, Any] | None = None,
) -> AsrRequest:
    """Build an immutable request from any chunk sequence.

    This helper keeps CLI code ergonomic while preserving tuple fields in the
    durable contract.
    """

    return AsrRequest(
        chunks=tuple(chunks),
        language=language,
        task=task,
        context=context,
        hotwords=tuple(hotwords),
        timestamps=timestamps,
        diarization=diarization,
        metadata={} if metadata is None else dict(metadata),
    )
