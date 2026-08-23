from __future__ import annotations

import pytest

from ja_media_core.subsync import subtitle_anchor_fit_score, subtitle_goodness_of_fit
from ja_media_core.transcripts import SubtitleCue


def test_goodness_of_fit_rewards_matching_intervals() -> None:
    reference = [_cue(1.0, 2.0), _cue(5.0, 6.0)]
    candidate = [_cue(1.0, 2.0), _cue(5.0, 6.0)]

    assert subtitle_goodness_of_fit(reference, candidate) == pytest.approx(2.0)


def test_goodness_of_fit_penalizes_length_mismatch_in_standard_mode() -> None:
    reference = [_cue(1.0, 3.0)]
    candidate = [_cue(1.0, 2.0)]

    assert subtitle_goodness_of_fit(reference, candidate) == pytest.approx(0.5)


def test_goodness_of_fit_overlap_mode_ignores_length_mismatch() -> None:
    reference = [_cue(1.0, 3.0)]
    candidate = [_cue(1.0, 2.0)]

    assert subtitle_goodness_of_fit(reference, candidate, mode="overlap") == pytest.approx(
        1e-5
    )


def test_goodness_of_fit_normalizes_overlapping_but_not_adjacent_cues() -> None:
    reference = [_cue(0.0, 1.0), _cue(1.0, 2.0), _cue(3.0, 5.0)]
    candidate = [_cue(0.0, 1.0), _cue(1.0, 2.0), _cue(3.0, 4.0), _cue(3.5, 5.0)]

    assert subtitle_goodness_of_fit(reference, candidate) == pytest.approx(3.0)


def test_goodness_of_fit_applies_candidate_shifts_and_split_penalty() -> None:
    reference = [_cue(1.0, 2.0), _cue(5.0, 6.0)]
    candidate = [_cue(0.0, 1.0), _cue(4.0, 5.0)]

    constant = subtitle_goodness_of_fit(reference, candidate, candidate_shifts=[1.0, 1.0])
    split = subtitle_goodness_of_fit(reference, candidate, candidate_shifts=[1.0, 1.1])

    assert constant == pytest.approx(2.0)
    assert split == pytest.approx(1.886, abs=1e-9)


def test_goodness_of_fit_rejects_unknown_shift_count() -> None:
    with pytest.raises(ValueError, match="candidate_shifts"):
        subtitle_goodness_of_fit([_cue(1.0, 2.0)], [_cue(1.0, 2.0)], candidate_shifts=[])


def test_anchor_fit_score_punishes_excessive_active_duration() -> None:
    reference = [_cue(1.0, 2.0), _cue(5.0, 6.0)]
    normal = [_cue(1.0, 2.0), _cue(5.0, 6.0)]
    bloated = [_cue(0.5, 2.5), _cue(4.5, 6.5)]

    assert subtitle_goodness_of_fit(reference, bloated) == pytest.approx(1.0)
    assert subtitle_anchor_fit_score(reference, bloated) == pytest.approx(0.25)
    assert subtitle_anchor_fit_score(reference, normal) > subtitle_anchor_fit_score(
        reference,
        bloated,
    )


def test_anchor_fit_score_punishes_duplicate_cue_density() -> None:
    reference = [_cue(1.0, 2.0), _cue(5.0, 6.0)]
    normal = [_cue(1.0, 2.0), _cue(5.0, 6.0)]
    duplicated = [
        _cue(1.0, 2.0),
        _cue(1.0, 2.0),
        _cue(5.0, 6.0),
        _cue(5.0, 6.0),
    ]

    assert subtitle_goodness_of_fit(reference, duplicated) == pytest.approx(2.0)
    assert subtitle_anchor_fit_score(reference, duplicated) == pytest.approx(0.4082483)
    assert subtitle_anchor_fit_score(reference, normal) > subtitle_anchor_fit_score(
        reference,
        duplicated,
    )


def test_anchor_fit_score_allows_one_third_more_cues_without_count_penalty() -> None:
    reference = [_cue(0.0, 3.0), _cue(4.0, 7.0), _cue(8.0, 11.0)]
    candidate = [
        _cue(0.0, 1.5),
        _cue(1.5, 3.0),
        _cue(4.0, 7.0),
        _cue(8.0, 11.0),
    ]

    assert subtitle_anchor_fit_score(reference, candidate) == pytest.approx(1.0)


def test_anchor_fit_score_mildly_penalizes_cues_beyond_deadband() -> None:
    reference = [_cue(0.0, 3.0), _cue(4.0, 7.0), _cue(8.0, 11.0)]
    candidate = [
        _cue(0.0, 1.5),
        _cue(1.5, 3.0),
        _cue(4.0, 5.5),
        _cue(5.5, 7.0),
        _cue(8.0, 11.0),
    ]

    assert subtitle_anchor_fit_score(reference, candidate) == pytest.approx(
        (4 / 5) ** 0.5
    )


def _cue(start_s: float, end_s: float) -> SubtitleCue:
    return SubtitleCue(
        source_path=None,
        index=1,
        start_s=start_s,
        end_s=end_s,
        text="subtitle",
    )
