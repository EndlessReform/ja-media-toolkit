from __future__ import annotations

import re
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Iterable, Literal

from ja_media_core.media_filename import first_positive_episode
from ja_media_core.transcripts import SubtitleCue

ANILIST_DIRECTORY_PATTERN = re.compile(r"^anilist-([1-9]\d*)$", re.IGNORECASE)
GoodnessOfFitMode = Literal["standard", "overlap"]


@dataclass(frozen=True)
class SubtitleCandidate:
    """One subtitle candidate for alignment or promotion.

    Carries the resolved filesystem path, parsed cues, and optional identifiers
    from the source repository (e.g. Kitsunekko repo path or a numeric ID).
    """

    path: Path
    cues: list[SubtitleCue]
    repo_path: str | None = None
    subtitle_id: str | None = None


def is_supported_subtitle_file(path: str | Path) -> bool:
    """Return ``True`` when the path has a recognized subtitle extension (.srt or .ass)."""

    ext = Path(path).suffix.lower().lstrip(".")
    return ext in {"srt", "ass"}


def infer_episode_number(filename_stem: str) -> int | None:
    """Extract an episode number from a media filename stem using PTN."""

    return first_positive_episode(filename_stem)


def infer_anilist_id(media_path: str | Path) -> int | None:
    """Read an AniList ID from the nearest ``anilist-<id>`` ancestor.

    Derived-audio libraries use the stable AniList identifier as their series
    directory name. Walking from the media file upward keeps the convention
    useful if later layouts add season or disc subdirectories.
    """

    path = Path(media_path).expanduser()
    directories = path.parents if path.suffix else (path, *path.parents)
    for directory in directories:
        match = ANILIST_DIRECTORY_PATTERN.fullmatch(directory.name)
        if match:
            return int(match.group(1))
    return None


def subtitle_goodness_of_fit(
    reference_cues: Iterable[SubtitleCue],
    candidate_cues: Iterable[SubtitleCue],
    *,
    candidate_shifts: Iterable[float] | None = None,
    mode: GoodnessOfFitMode = "standard",
    split_coefficient: float = 7.0,
) -> float:
    """Score how well shifted candidate cue intervals overlap a reference.

    This is the ALASS-style value function described in
    ``docs/goodness_of_fit.md``. It deliberately does not infer timing shifts;
    callers pass cues that are already in their proposed positions, or provide
    per-cue ``candidate_shifts`` to evaluate a known shift sequence.
    """

    if mode not in {"standard", "overlap"}:
        raise ValueError("mode must be 'standard' or 'overlap'")

    references = _normalized_cue_intervals(reference_cues)
    candidates, split_count = _shifted_candidate_intervals(
        candidate_cues,
        candidate_shifts,
    )
    if not references or not candidates:
        return 0.0

    score = 0.0
    ref_index = 0
    candidate_index = 0
    while ref_index < len(references) and candidate_index < len(candidates):
        ref_start, ref_end = references[ref_index]
        cand_start, cand_end = candidates[candidate_index]
        overlap = min(ref_end, cand_end) - max(ref_start, cand_start)
        if overlap > 0:
            if mode == "overlap":
                score += overlap * 1e-5
            else:
                score += overlap / max(ref_end - ref_start, cand_end - cand_start)
        if ref_end <= cand_end:
            ref_index += 1
        else:
            candidate_index += 1

    split_penalty = min(len(references), len(candidates)) * split_coefficient / 1000
    return score - split_count * split_penalty


def subtitle_anchor_fit_score(
    reference_cues: Iterable[SubtitleCue],
    candidate_cues: Iterable[SubtitleCue],
) -> float:
    """Return a normalized candidate-ranking score against a timing anchor.

    The pure goodness-of-fit score rewards interval overlap but does not care
    when a candidate carries far more subtitle mass than the anchor. That makes
    multi-language or duplicate-line releases look deceptively good. This
    ranking score keeps overlap as the base value, then applies precision-style
    penalties for excessive raw active duration and cue count. The result is
    normalized by the reference span count so 1.0 means a perfect anchor match.
    """

    references = list(reference_cues)
    candidates = list(candidate_cues)
    reference_count = _nonzero_cue_count(references)
    if reference_count == 0:
        return 0.0
    score = subtitle_goodness_of_fit(references, candidates)
    if score <= 0:
        return score

    active_precision = _precision_ratio(
        _raw_active_duration_s(references),
        _raw_active_duration_s(candidates),
    )
    count_precision = _precision_ratio(
        _nonzero_cue_count(references),
        _nonzero_cue_count(candidates),
    )
    return score * active_precision * sqrt(count_precision) / reference_count


def _precision_ratio(reference_value: float | int, candidate_value: float | int) -> float:
    if reference_value <= 0 or candidate_value <= reference_value:
        return 1.0
    return reference_value / candidate_value


def _raw_active_duration_s(cues: Iterable[SubtitleCue]) -> float:
    return sum(max(0.0, cue.end_s - cue.start_s) for cue in cues)


def _nonzero_cue_count(cues: Iterable[SubtitleCue]) -> int:
    return sum(1 for cue in cues if cue.end_s > cue.start_s)


def _shifted_candidate_intervals(
    cues: Iterable[SubtitleCue],
    shifts: Iterable[float] | None,
) -> tuple[list[tuple[float, float]], int]:
    cue_list = list(cues)
    if shifts is None:
        return _normalized_cue_intervals(cue_list), 0

    shift_list = list(shifts)
    if len(shift_list) != len(cue_list):
        raise ValueError("candidate_shifts must match candidate cue count")

    shifted = []
    retained_shifts = []
    for cue, shift in zip(cue_list, shift_list, strict=True):
        if cue.end_s <= cue.start_s:
            continue
        shifted.append((cue.start_s + shift, cue.end_s + shift))
        retained_shifts.append(shift)
    split_count = sum(
        1
        for previous, current in zip(retained_shifts, retained_shifts[1:], strict=False)
        if previous != current
    )
    return _normalize_intervals(shifted), split_count


def _normalized_cue_intervals(
    cues: Iterable[SubtitleCue],
) -> list[tuple[float, float]]:
    return _normalize_intervals(
        (cue.start_s, cue.end_s) for cue in cues if cue.end_s > cue.start_s
    )


def _normalize_intervals(
    intervals: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []
    for start_s, end_s in sorted(intervals):
        if end_s <= start_s:
            continue
        if normalized and start_s < normalized[-1][1]:
            previous_start, previous_end = normalized[-1]
            normalized[-1] = (previous_start, max(previous_end, end_s))
        else:
            normalized.append((start_s, end_s))
    return normalized
