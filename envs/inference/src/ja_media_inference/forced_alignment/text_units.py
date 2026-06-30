from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ja_media_core.forced_alignment import (
    AlignmentDiagnostic,
    AlignmentSpan,
    SourceCueRef,
    SpanAlignment,
    source_cue_ref_from_cue,
)
from ja_media_core.transcripts import SubtitleCue


@dataclass(frozen=True)
class AlignmentTextGroup:
    """One caller-owned text span that may be split into aligner tokens.

    A group can be an SRT/ASS cue, one ASR reference paragraph, a TTS source
    sentence, or any other span whose aggregate timing should be recovered from
    word-level predictions.
    """

    id: str
    text: str
    source_cue: SourceCueRef | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("alignment text groups must include an id")
        if not self.text:
            raise ValueError("alignment text groups must include text")


@dataclass(frozen=True)
class AlignmentToken:
    """One prompt token/span sent to the Qwen forced aligner."""

    id: str
    text: str
    group_id: str
    group_index: int
    char_start: int | None = None
    char_end: int | None = None
    source_cue: SourceCueRef | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_core_span(self) -> AlignmentSpan:
        return AlignmentSpan(
            id=self.id,
            text=self.text,
            source_cue=self.source_cue,
            cue_char_start=self.char_start,
            cue_char_end=self.char_end,
            metadata={
                **self.metadata,
                "group_id": self.group_id,
                "group_index": self.group_index,
            },
        )


@dataclass(frozen=True)
class TokenAlignment:
    """Predicted timing for one aligner token."""

    token: AlignmentToken
    start_s: float
    end_s: float
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_core_alignment(self) -> SpanAlignment:
        status = "aligned" if self.end_s >= self.start_s else "suspicious"
        diagnostics: tuple[AlignmentDiagnostic, ...] = ()
        if status != "aligned":
            diagnostics = (
                AlignmentDiagnostic(
                    code="non_monotonic_token_timestamp",
                    message="Predicted token end is before its start.",
                    severity="warning",
                    span_id=self.token.id,
                    source_cue=self.token.source_cue,
                ),
            )
        return SpanAlignment(
            span_id=self.token.id,
            start_s=self.start_s,
            end_s=self.end_s,
            status=status,
            confidence=self.confidence,
            diagnostics=diagnostics,
            metadata=dict(self.metadata),
        )


def groups_from_text(
    text: str,
    *,
    group_id: str = "text-0001",
    metadata: dict[str, Any] | None = None,
) -> list[AlignmentTextGroup]:
    """Build one alignment group from untimed source text."""

    return [
        AlignmentTextGroup(
            id=group_id,
            text=text,
            metadata={} if metadata is None else dict(metadata),
        )
    ]


def groups_from_lines(
    text: str,
    *,
    group_prefix: str = "line",
    metadata: dict[str, Any] | None = None,
) -> list[AlignmentTextGroup]:
    """Build one untimed alignment group per non-empty source-text line."""

    groups: list[AlignmentTextGroup] = []
    base_metadata = {} if metadata is None else dict(metadata)
    for index, line in enumerate(
        (raw_line.strip() for raw_line in text.splitlines()),
        start=1,
    ):
        if not line:
            continue
        groups.append(
            AlignmentTextGroup(
                id=f"{group_prefix}:{index:04d}",
                text=line,
                metadata={**base_metadata, "source_line": index},
            )
        )
    return groups


def groups_from_cues(
    cues: Iterable[SubtitleCue],
    *,
    source_id: str,
) -> list[AlignmentTextGroup]:
    """Build one text group per subtitle cue."""

    groups: list[AlignmentTextGroup] = []
    for cue in cues:
        cue_ref = source_cue_ref_from_cue(cue, source_id=source_id)
        groups.append(
            AlignmentTextGroup(
                id=f"{source_id}:cue:{cue.index}",
                text=cue.text,
                source_cue=cue_ref,
                metadata={
                    "source": "subtitle_cue",
                    "cue_index": cue.index,
                    "cue_start_s": cue.start_s,
                    "cue_end_s": cue.end_s,
                },
            )
        )
    return groups


def segment_group_with_nagisa(
    group: AlignmentTextGroup,
    *,
    exclude_postags: frozenset[str] = frozenset({"補助記号"}),
) -> list[AlignmentToken]:
    """Segment a text group with nagisa while preserving group membership.

    Japanese punctuation is skipped by default because Qwen predicts it as
    boundary evidence rather than durable spoken material. Callers that need
    karaoke-style punctuation handling can pass an empty ``exclude_postags`` and
    decide how to merge or display those tokens downstream.
    """

    try:
        import nagisa
    except ImportError as exc:
        raise RuntimeError(
            "nagisa is required for Japanese alignment tokenization"
        ) from exc

    tagged = nagisa.tagging(group.text)
    tagged_items = [
        (word, postag)
        for word, postag in zip(tagged.words, tagged.postags, strict=True)
        if word.strip() and postag not in exclude_postags
    ]
    words = [word for word, _postag in tagged_items]
    offsets = _greedy_offsets(group.text, words)
    tokens: list[AlignmentToken] = []
    for index, ((word, postag), bounds) in enumerate(
        zip(tagged_items, offsets, strict=True)
    ):
        char_start, char_end = bounds
        tokens.append(
            AlignmentToken(
                id=f"{group.id}:tok:{index:04d}",
                text=word,
                group_id=group.id,
                group_index=index,
                char_start=char_start,
                char_end=char_end,
                source_cue=group.source_cue,
                metadata={"segmenter": "nagisa", "postag": postag},
            )
        )
    return tokens


def merge_token_alignments_by_group(
    groups: Iterable[AlignmentTextGroup],
    token_alignments: Iterable[TokenAlignment],
) -> dict[str, SpanAlignment]:
    """Gobble word-level timings back into caller-owned group timings."""

    alignments_by_group: dict[str, list[TokenAlignment]] = {}
    for alignment in token_alignments:
        alignments_by_group.setdefault(alignment.token.group_id, []).append(alignment)

    merged: dict[str, SpanAlignment] = {}
    for group in groups:
        items = sorted(
            alignments_by_group.get(group.id, ()),
            key=lambda item: item.token.group_index,
        )
        if not items:
            merged[group.id] = SpanAlignment(
                span_id=group.id,
                start_s=None,
                end_s=None,
                status="missing",
                diagnostics=(
                    AlignmentDiagnostic(
                        code="no_group_tokens",
                        message="No token predictions were available for the group.",
                        source_cue=group.source_cue,
                    ),
                ),
            )
            continue
        start_s = min(item.start_s for item in items)
        end_s = max(item.end_s for item in items)
        merged[group.id] = SpanAlignment(
            span_id=group.id,
            start_s=start_s,
            end_s=end_s,
            status="aligned" if end_s >= start_s else "suspicious",
            metadata={
                "token_ids": [item.token.id for item in items],
                "token_count": len(items),
            },
        )
    return merged


def _greedy_offsets(text: str, words: list[str]) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for word in words:
        start = text.find(word, cursor)
        if start < 0:
            offsets.append((cursor, cursor + len(word)))
            cursor += len(word)
            continue
        end = start + len(word)
        offsets.append((start, end))
        cursor = end
    return offsets
