"""Window planning checks for the prepared-case runner."""

from ja_media_inference.forced_alignment.case_runner import (
    _retimed_cues,
    _window_starts,
    select_targets,
)


def test_selects_same_fixed_cues_for_window_comparison() -> None:
    records = [
        {
            "cue_id": f"cue:{index}",
            "source_start_s": float(index * 10),
            "source_end_s": float(index * 10 + 2),
        }
        for index in range(1, 10)
    ]

    targets = select_targets(records, 100.0)

    assert [row["cue_id"] for row in targets.values()] == ["cue:2", "cue:5", "cue:8"]


def test_full_windows_are_consecutive_and_unpadded() -> None:
    assert _window_starts(370.0, 180.0) == [0.0, 180.0, 360.0]


def test_retimed_srt_uses_the_text_that_was_aligned() -> None:
    records = [{"cue_id": "cue:1", "cleaned_index": 1, "alignment_text": "LLM"}]
    aligned = {
        "cue:1": {
            "aligned_start_s": 1.0,
            "aligned_end_s": 2.0,
            "text": "deterministic",
        }
    }

    assert _retimed_cues(records, aligned)[0].text == "deterministic"
