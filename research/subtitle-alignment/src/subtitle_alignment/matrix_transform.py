"""Infer inspectable transforms from original and aligned cue clocks."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import median

from ja_media_core.transcripts import SubtitleCue


@dataclass(frozen=True)
class TransformFacts:
    """Whole-clock scale plus per-cue translations realized by an aligner."""

    scale: float
    median_offset_s: float
    min_offset_s: float
    max_offset_s: float
    max_abs_offset_s: float
    offset_blocks: int
    reconstruction_rmse_ms: float


def infer_transform(
    original: tuple[SubtitleCue, ...],
    aligned: tuple[SubtitleCue, ...],
    *,
    block_tolerance_s: float = 0.021,
) -> tuple[TransformFacts, list[dict[str, object]]]:
    """Recover scale and cue translations while retaining each cue's offset."""

    if len(original) != len(aligned):
        raise ValueError(
            f"cue count changed from {len(original)} to {len(aligned)}"
        )
    duration_ratios = [
        shifted.duration_s / source.duration_s
        for source, shifted in zip(original, aligned, strict=True)
        if source.duration_s > 0.001
    ]
    scale = median(duration_ratios) if duration_ratios else 1.0
    offsets = [
        shifted.start_s - scale * source.start_s
        for source, shifted in zip(original, aligned, strict=True)
    ]
    if not offsets:
        raise ValueError("aligned output contains no cues")
    blocks = 1 + sum(
        abs(current - previous) > block_tolerance_s
        for previous, current in zip(offsets, offsets[1:], strict=False)
    )
    errors = []
    cue_rows = []
    for index, (source, shifted, offset) in enumerate(
        zip(original, aligned, offsets, strict=True)
    ):
        predicted_start = scale * source.start_s + offset
        predicted_end = scale * source.end_s + offset
        errors.extend(
            [shifted.start_s - predicted_start, shifted.end_s - predicted_end]
        )
        cue_rows.append(
            {
                "cue_index": index,
                "source_start_s": source.start_s,
                "source_end_s": source.end_s,
                "aligned_start_s": shifted.start_s,
                "aligned_end_s": shifted.end_s,
                "scale": scale,
                "offset_s": offset,
                "offset_block_start": index == 0
                or abs(offset - offsets[index - 1]) > block_tolerance_s,
            }
        )
    facts = TransformFacts(
        scale=scale,
        median_offset_s=median(offsets),
        min_offset_s=min(offsets),
        max_offset_s=max(offsets),
        max_abs_offset_s=max(abs(value) for value in offsets),
        offset_blocks=blocks,
        reconstruction_rmse_ms=sqrt(
            sum(error * error for error in errors) / len(errors)
        )
        * 1000,
    )
    return facts, cue_rows
