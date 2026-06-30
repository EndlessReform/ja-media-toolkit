from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Sequence

from ja_media_core.audio import AudioChunk
from ja_media_core.transcripts import SubtitleCue


AlignmentRunPurpose = Literal[
    "candidate_selection",
    "word_subtitles",
    "authoritative_retiming",
    "timing_eval",
    "untimed_text_alignment",
]
AlignmentStatus = Literal["aligned", "missing", "suspicious", "failed"]
DiagnosticSeverity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class AlignmentArtifactRef:
    """Portable pointer to an input or output artifact.

    The URI is the durable identity. It may be a local ``file://`` URI today and
    an ``s3://`` URI later. Hash and size fields let run readers detect stale or
    mismatched artifacts before trusting downstream timing metrics.
    """

    uri: str
    media_type: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceCueRef:
    """Stable breadcrumb from alignment spans back to their source cue.

    ``source_id`` is workflow-owned and should be stable inside a run, such as a
    candidate subtitle id or transcript artifact id. ``cue_index`` preserves the
    human-facing subtitle index. ``cue_block_index`` preserves parse order when
    the source format has missing or duplicate indexes.
    """

    source_id: str
    cue_index: int
    cue_block_index: int | None = None
    source_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlignmentSpan:
    """One text unit requested from a forced-aligner backend.

    A span is usually a lexical token or short phrase, but core intentionally
    does not define Japanese word segmentation. The important permanent
    contract is that every backend result points back to this span id, and the
    span points back to the cue and character range that created it.
    """

    id: str
    text: str
    source_cue: SourceCueRef | None = None
    cue_char_start: int | None = None
    cue_char_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("alignment spans must have a non-empty id")
        if not self.text:
            raise ValueError("alignment spans must have non-empty text")
        if (self.cue_char_start is None) != (self.cue_char_end is None):
            raise ValueError("cue character ranges must include both start and end")
        if (
            self.cue_char_start is not None
            and self.cue_char_end is not None
            and self.cue_char_end < self.cue_char_start
        ):
            raise ValueError("cue character range end must not be before start")


@dataclass(frozen=True)
class AlignmentWindow:
    """One audio/text alignment request in source-clock coordinates.

    Windows are the backend-facing unit of work. Durable runs can persist the
    same object before execution, which makes failed windows replayable without
    requiring the backend to know about manifests or workspace layouts.
    """

    id: str
    audio: AudioChunk
    spans: tuple[AlignmentSpan, ...]
    language: str | None = "ja"
    context: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("alignment windows must have a non-empty id")
        if not self.spans:
            raise ValueError("alignment windows must include at least one span")


@dataclass(frozen=True)
class AlignmentDiagnostic:
    """Machine-readable explanation attached to a run, window, or span.

    Diagnostics are deliberately structured instead of free-form logs so later
    tooling can answer questions such as "which cue failed because timestamps
    were non-monotonic?" or "which backend produced missing spans?"
    """

    code: str
    message: str
    severity: DiagnosticSeverity = "warning"
    window_id: str | None = None
    span_id: str | None = None
    source_cue: SourceCueRef | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpanAlignment:
    """Predicted timing for one requested alignment span."""

    span_id: str
    start_s: float | None
    end_s: float | None
    status: AlignmentStatus = "aligned"
    confidence: float | None = None
    diagnostics: tuple[AlignmentDiagnostic, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.span_id:
            raise ValueError("span alignments must include a span id")
        if self.status == "aligned":
            if self.start_s is None or self.end_s is None:
                raise ValueError("aligned spans must include start and end times")
            if self.end_s < self.start_s:
                raise ValueError("aligned span end must not be before start")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("alignment confidence must be between 0.0 and 1.0")


@dataclass(frozen=True)
class AlignmentWindowResult:
    """Backend output for one alignment window."""

    window_id: str
    backend: str
    status: Literal["succeeded", "partial", "failed"]
    alignments: tuple[SpanAlignment, ...] = ()
    diagnostics: tuple[AlignmentDiagnostic, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlignmentRunManifest:
    """Small replay index for a durable forced-alignment run.

    The manifest is intentionally not the result table. It records why the run
    exists, which artifacts and backend settings produced it, and where to find
    request/result/evaluation artifacts.
    """

    id: str
    purpose: AlignmentRunPurpose
    schema_version: int
    created_at: str
    inputs: tuple[AlignmentArtifactRef, ...]
    backend: dict[str, Any]
    span_policy: str
    window_policy: dict[str, Any]
    outputs: tuple[AlignmentArtifactRef, ...] = ()
    diagnostics: tuple[AlignmentDiagnostic, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class ForcedAlignerBackend(Protocol):
    """Synchronous backend over core forced-alignment contracts."""

    name: str

    def align(
        self,
        windows: Sequence[AlignmentWindow],
    ) -> list[AlignmentWindowResult]:
        ...


def source_cue_ref_from_cue(cue: SubtitleCue, *, source_id: str) -> SourceCueRef:
    """Build a run-local cue reference from the native subtitle cue object."""

    block_index = cue.metadata.get("block_index")
    return SourceCueRef(
        source_id=source_id,
        cue_index=cue.index,
        cue_block_index=block_index if isinstance(block_index, int) else None,
        source_path=cue.source_path,
    )
