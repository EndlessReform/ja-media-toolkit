"""Window planning checks for the prepared-case runner."""

from threading import Lock
import time

from ja_media_inference.forced_alignment.case_runner import (
    _map_concurrently_in_order,
    _retimed_cues,
    select_targets,
)
from ja_media_inference.forced_alignment.text_units import TokenAlignment
from ja_media_inference.forced_alignment.window_execution import align_remote_window


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


def test_window_jobs_run_concurrently_but_return_in_planner_order() -> None:
    lock = Lock()
    active = 0
    peak_active = 0

    def run(job: int) -> int:
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep((4 - job) * 0.005)
        with lock:
            active -= 1
        return job

    results = _map_concurrently_in_order(
        [1, 2, 3], concurrency=3, operation=run
    )

    assert peak_active == 3
    assert results == [1, 2, 3]


def test_remote_window_projects_crop_local_tokens_to_episode_clock() -> None:
    records = [
        {
            "cue_id": "cue:1",
            "source_index": 1,
            "cleaned_index": 1,
            "source_start_s": 100.0,
            "source_end_s": 102.0,
            "alignment_text": "普通の台詞です",
        }
    ]

    result = align_remote_window(
        FixedAdapter(),
        "a" * 64,
        records,
        target_id="cue:1",
        target_name="test",
        crop_start_s=90.0,
        crop_end_s=150.0,
    )

    target = result["target_result"]
    assert target["aligned_start_s"] == 91.0
    assert target["aligned_end_s"] == 92.0
    assert target["score_signals"]["min_endpoint_max_probability"] == 0.8


class FixedAdapter:
    def align_crop(self, *, tokens, **_options):  # type: ignore[no-untyped-def]
        return [
            TokenAlignment(
                token=token,
                start_s=1.0,
                end_s=2.0,
                confidence=0.8,
                metadata={
                    "start_distribution": _distribution(),
                    "end_distribution": _distribution(),
                },
            )
            for token in tokens
        ]


def _distribution() -> dict[str, float]:
    return {
        "max_probability": 0.8,
        "top_two_probability_margin": 0.5,
        "normalized_entropy": 0.2,
        "edge_distance_s": 1.0,
    }
