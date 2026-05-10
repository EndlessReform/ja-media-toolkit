from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from ja_media_core.audio import AudioChunk


@dataclass(frozen=True)
class VadOptions:
    threshold: float | None = None
    min_speech_s: float = 0.25
    min_silence_s: float = 0.20
    speech_pad_s: float = 0.05
    merge_gap_s: float = 0.10
    channel: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeechSpan:
    start_s: float
    end_s: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class VadTimeline:
    chunk: AudioChunk
    speech: list[SpeechSpan]
    backend: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_speech_spans(self.speech)
        if self.speech:
            if self.speech[0].start_s < self.chunk.start_s:
                raise ValueError("VAD speech starts before the source chunk")
            if self.speech[-1].end_s > self.chunk.end_s:
                raise ValueError("VAD speech ends after the source chunk")


class VadBackend(Protocol):
    name: str

    def detect(
        self,
        chunks: Sequence[AudioChunk],
        *,
        options: VadOptions | None = None,
    ) -> list[VadTimeline]:
        ...


@dataclass(frozen=True)
class _CutSearch:
    target_s: float
    radius_s: float = 60.0
    min_silence_s: float = 0.20
    prefer_before_target: bool = False


@dataclass(frozen=True)
class _CutCandidate:
    cut_s: float
    score: float
    reason: str
    distance_from_target_s: float
    silence_start_s: float | None = None
    silence_end_s: float | None = None
    fallback: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def validate_speech_spans(spans: Sequence[SpeechSpan]) -> None:
    previous_end_s: float | None = None
    for span in spans:
        if span.end_s <= span.start_s:
            raise ValueError("VAD speech spans must have positive duration")
        if previous_end_s is not None and span.start_s < previous_end_s:
            raise ValueError("VAD speech spans must be monotonic and non-overlapping")
        previous_end_s = span.end_s


def normalize_speech_spans(
    spans: Sequence[SpeechSpan],
    *,
    start_s: float,
    end_s: float,
    min_duration_s: float = 0.0,
    merge_gap_s: float = 0.0,
    pad_s: float = 0.0,
) -> list[SpeechSpan]:
    if end_s < start_s:
        raise ValueError("VAD normalization bounds must be monotonic")

    clipped: list[SpeechSpan] = []
    for span in sorted(spans, key=lambda item: (item.start_s, item.end_s)):
        clipped_start_s = max(start_s, span.start_s - pad_s)
        clipped_end_s = min(end_s, span.end_s + pad_s)
        if clipped_end_s <= clipped_start_s:
            continue
        clipped.append(
            SpeechSpan(
                start_s=clipped_start_s,
                end_s=clipped_end_s,
                metadata=dict(span.metadata),
            )
        )

    merged: list[SpeechSpan] = []
    for span in clipped:
        if not merged or span.start_s > merged[-1].end_s + merge_gap_s:
            merged.append(span)
            continue

        previous = merged[-1]
        merged[-1] = SpeechSpan(
            start_s=previous.start_s,
            end_s=max(previous.end_s, span.end_s),
            metadata=_merge_metadata(previous.metadata, span.metadata),
        )

    return [
        span
        for span in merged
        if span.end_s - span.start_s >= min_duration_s
    ]


def speech_chunks_from_timeline(
    timeline: VadTimeline,
    *,
    min_duration_s: float = 0.0,
    kind: str = "speech",
    metadata: dict[str, Any] | None = None,
) -> list[AudioChunk]:
    spans = normalize_speech_spans(
        timeline.speech,
        start_s=timeline.chunk.start_s,
        end_s=timeline.chunk.end_s,
        min_duration_s=min_duration_s,
    )
    chunks: list[AudioChunk] = []
    base_metadata = {} if metadata is None else dict(metadata)

    for index, span in enumerate(spans):
        chunk_metadata = {
            **base_metadata,
            **span.metadata,
            "boundary_source": "vad",
            "vad_backend": timeline.backend,
            "vad_parent_kind": timeline.chunk.kind,
            "vad_span_index": index,
        }
        start_frame = _seconds_to_frame(span.start_s, timeline.chunk)
        end_frame = _seconds_to_frame(span.end_s, timeline.chunk)
        chunks.append(
            AudioChunk(
                source=timeline.chunk.source,
                start_s=span.start_s,
                end_s=span.end_s,
                source_start_frame=start_frame,
                source_end_frame=end_frame,
                format=timeline.chunk.format,
                kind=kind,
                metadata=chunk_metadata,
            )
        )

    return chunks


def speech_chunks_from_timelines(
    timelines: Sequence[VadTimeline],
    *,
    min_duration_s: float = 0.0,
    kind: str = "speech",
    metadata: dict[str, Any] | None = None,
) -> list[AudioChunk]:
    chunks: list[AudioChunk] = []
    for timeline in timelines:
        chunks.extend(
            speech_chunks_from_timeline(
                timeline,
                min_duration_s=min_duration_s,
                kind=kind,
                metadata=metadata,
            )
        )
    return chunks


def plan_vad_splits(
    chunk: AudioChunk,
    backend: VadBackend,
    *,
    every_s: float,
    search_radius_s: float = 60.0,
    vad_options: VadOptions | None = None,
    min_silence_s: float | None = None,
    prefer_before_target: bool = False,
    kind: str = "asr_chunk",
    metadata: dict[str, Any] | None = None,
) -> list[AudioChunk]:
    if every_s <= 0:
        raise ValueError("Split interval must be positive")
    if search_radius_s <= 0:
        raise ValueError("Split search radius must be positive")

    target_s = chunk.start_s + every_s
    targets: list[float] = []
    while target_s < chunk.end_s:
        targets.append(target_s)
        target_s += every_s

    cuts: list[_CutCandidate] = []
    active_options = VadOptions() if vad_options is None else vad_options
    active_min_silence_s = (
        active_options.min_silence_s
        if min_silence_s is None
        else min_silence_s
    )

    for target_s in targets:
        search_chunk = _subchunk(
            chunk,
            start_s=max(chunk.start_s, target_s - search_radius_s),
            end_s=min(chunk.end_s, target_s + search_radius_s),
            kind="vad_search_window",
            metadata={
                "purpose": "vad_split_search",
                "target_s": target_s,
                "search_radius_s": search_radius_s,
            },
        )
        timeline = backend.detect([search_chunk], options=active_options)[0]
        cuts.append(
            _choose_cut(
                timeline,
                _CutSearch(
                    target_s=target_s,
                    radius_s=search_radius_s,
                    min_silence_s=active_min_silence_s,
                    prefer_before_target=prefer_before_target,
                ),
            )
        )

    return _chunks_from_cuts(
        chunk,
        cuts,
        kind=kind,
        metadata={} if metadata is None else dict(metadata),
    )


def _choose_cut(timeline: VadTimeline, search: _CutSearch) -> _CutCandidate:
    search_start_s = max(timeline.chunk.start_s, search.target_s - search.radius_s)
    search_end_s = min(timeline.chunk.end_s, search.target_s + search.radius_s)
    if search_end_s < search_start_s:
        raise ValueError("Cut search window is outside the VAD timeline")

    gaps = _speech_gaps(
        timeline.speech,
        start_s=search_start_s,
        end_s=search_end_s,
        min_silence_s=search.min_silence_s,
    )
    if not gaps:
        return _CutCandidate(
            cut_s=_clamp(search.target_s, search_start_s, search_end_s),
            score=0.0,
            reason="no qualifying silence",
            distance_from_target_s=0.0,
            fallback=True,
            metadata={"target_s": search.target_s},
        )

    best_gap = min(
        gaps,
        key=lambda gap: (
            _distance_to_gap(search.target_s, gap),
            -1 * (gap[1] - gap[0]),
            _direction_tiebreak(search.target_s, gap, search.prefer_before_target),
        ),
    )
    cut_s = _clamp(search.target_s, best_gap[0], best_gap[1])
    distance = abs(cut_s - search.target_s)
    silence_duration_s = best_gap[1] - best_gap[0]

    return _CutCandidate(
        cut_s=cut_s,
        score=1.0 / (1.0 + distance),
        reason="nearest qualifying silence",
        distance_from_target_s=distance,
        silence_start_s=best_gap[0],
        silence_end_s=best_gap[1],
        fallback=False,
        metadata={
            "target_s": search.target_s,
            "silence_duration_s": silence_duration_s,
        },
    )


def _chunks_from_cuts(
    chunk: AudioChunk,
    cuts: Sequence[_CutCandidate],
    *,
    kind: str,
    metadata: dict[str, Any],
) -> list[AudioChunk]:
    boundaries = [chunk.start_s, *[cut.cut_s for cut in cuts], chunk.end_s]
    chunks: list[AudioChunk] = []
    for index, (start_s, end_s) in enumerate(zip(boundaries, boundaries[1:])):
        if end_s <= start_s:
            continue
        cut_metadata = metadata.copy()
        cut_metadata.update(
            {
                "boundary_source": "vad",
                "split_index": index,
            }
        )
        if index < len(cuts):
            cut = cuts[index]
            cut_metadata.update(
                {
                    "next_target_s": cut.metadata.get("target_s"),
                    "next_cut_s": cut.cut_s,
                    "next_cut_fallback": cut.fallback,
                    "next_cut_reason": cut.reason,
                    "next_cut_distance_from_target_s": cut.distance_from_target_s,
                    "next_cut_silence_start_s": cut.silence_start_s,
                    "next_cut_silence_end_s": cut.silence_end_s,
                }
            )

        chunks.append(
            AudioChunk(
                source=chunk.source,
                start_s=start_s,
                end_s=end_s,
                source_start_frame=_seconds_to_frame(start_s, chunk),
                source_end_frame=_seconds_to_frame(end_s, chunk),
                format=chunk.format,
                kind=kind,
                metadata=cut_metadata,
            )
        )
    return chunks


def _subchunk(
    chunk: AudioChunk,
    *,
    start_s: float,
    end_s: float,
    kind: str,
    metadata: dict[str, Any],
) -> AudioChunk:
    return AudioChunk(
        source=chunk.source,
        start_s=start_s,
        end_s=end_s,
        source_start_frame=_seconds_to_frame(start_s, chunk),
        source_end_frame=_seconds_to_frame(end_s, chunk),
        format=chunk.format,
        kind=kind,
        metadata={**chunk.metadata, **metadata},
    )


def _speech_gaps(
    speech: Sequence[SpeechSpan],
    *,
    start_s: float,
    end_s: float,
    min_silence_s: float,
) -> list[tuple[float, float]]:
    cursor_s = start_s
    gaps: list[tuple[float, float]] = []
    for span in speech:
        if span.end_s <= start_s:
            continue
        if span.start_s >= end_s:
            break
        speech_start_s = max(start_s, span.start_s)
        if speech_start_s - cursor_s >= min_silence_s:
            gaps.append((cursor_s, speech_start_s))
        cursor_s = max(cursor_s, min(end_s, span.end_s))

    if end_s - cursor_s >= min_silence_s:
        gaps.append((cursor_s, end_s))
    return gaps


def _seconds_to_frame(timestamp_s: float, chunk: AudioChunk) -> int | None:
    if chunk.format is None:
        return None
    return round(timestamp_s * chunk.format.sample_rate_hz)


def _merge_metadata(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if left == right:
        return dict(left)
    return {"merged": [dict(left), dict(right)]}


def _distance_to_gap(target_s: float, gap: tuple[float, float]) -> float:
    return abs(_clamp(target_s, gap[0], gap[1]) - target_s)


def _direction_tiebreak(
    target_s: float,
    gap: tuple[float, float],
    prefer_before_target: bool,
) -> float:
    midpoint_s = (gap[0] + gap[1]) / 2
    is_before = midpoint_s <= target_s
    return 0.0 if is_before == prefer_before_target else 1.0


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
