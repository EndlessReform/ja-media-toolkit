"""Checks for VAD-owned alignment windows and duplicate reconciliation."""

import pytest

from ja_media_inference.forced_alignment.window_planner import (
    plan_alignment_windows,
    records_for_window,
    select_alignment_candidates,
)


def test_vad_cores_stay_unpadded_with_a_short_boundary_probe() -> None:
    payload = {"split_chunks": [_chunk(0, 60), _chunk(60, 120)]}

    windows = plan_alignment_windows(payload, duration_s=120, boundary_radius_s=12)

    assert [item.kind for item in windows] == ["core", "boundary", "core"]
    assert [(item.crop_start_s, item.crop_end_s) for item in windows] == [
        (0, 60),
        (48, 72),
        (60, 120),
    ]


def test_boundary_cue_is_context_in_both_requests() -> None:
    payload = {"split_chunks": [_chunk(0, 60), _chunk(60, 120)]}
    windows = plan_alignment_windows(payload, duration_s=120, boundary_radius_s=12)
    records = [_cue("bridge", 57, 61), _cue("later", 80, 82)]

    assert [row["cue_id"] for row in records_for_window(records, windows[0])] == [
        "bridge"
    ]
    assert [row["cue_id"] for row in records_for_window(records, windows[1])] == [
        "bridge"
    ]
    assert [row["cue_id"] for row in records_for_window(records, windows[2])] == [
        "later"
    ]


def test_final_core_receives_cues_timed_after_canonical_audio() -> None:
    payload = {"split_chunks": [_chunk(0, 60), _chunk(60, 120)]}
    final_core = plan_alignment_windows(
        payload, duration_s=120, boundary_radius_s=12
    )[-1]
    records = [_cue("inside", 110, 112), _cue("after", 145, 147)]

    assert [
        row["cue_id"]
        for row in records_for_window(records, final_core, duration_s=120)
    ] == ["inside", "after"]


def test_reconciliation_keeps_plausible_owning_core_despite_token_diagnostics() -> None:
    records = [_cue("bridge", 57, 61)]
    first = _window_result(1, 0, 60, _result("bridge", "suspicious", edge=20))
    second = _window_result(2, 60, 120, _result("bridge", "aligned", edge=8))

    selected = select_alignment_candidates(records, [first, second])

    assert selected[0]["alignment_window_index"] == 1
    assert selected[0]["alignment_candidate_count"] == 2


def test_reconciliation_rejects_edge_bound_catastrophe_before_status() -> None:
    records = [_cue("bridge", 57, 61)]
    plausible = _window_result(1, 0, 60, _result("bridge", "suspicious", edge=8))
    catastrophic = _window_result(2, 60, 120, _result("bridge", "aligned", edge=0))
    catastrophic["cues"][0]["score_signals"]["aligned_duration_s"] = 34.0

    selected = select_alignment_candidates(records, [plausible, catastrophic])

    assert selected[0]["alignment_window_index"] == 1


def test_boundary_probe_replaces_geometrically_broken_owning_core() -> None:
    records = [_cue("bridge", 57, 61)]
    core = _window_result(1, 0, 60, _result("bridge", "aligned", edge=0))
    core["cues"][0]["score_signals"]["aligned_duration_s"] = 34.0
    boundary = _window_result(2, 0, 60, _result("bridge", "aligned", edge=8))
    boundary["window_kind"] = "boundary"

    selected = select_alignment_candidates(records, [core, boundary])

    assert selected[0]["alignment_window_index"] == 2


def test_reconciliation_keeps_owning_core_when_candidates_are_equally_healthy() -> None:
    records = [_cue("bridge", 57, 61)]
    core = _window_result(1, 0, 60, _result("bridge", "aligned", edge=2))
    boundary = _window_result(2, 0, 60, _result("bridge", "aligned", edge=20))
    boundary["window_kind"] = "boundary"

    selected = select_alignment_candidates(records, [core, boundary])

    assert selected[0]["alignment_window_index"] == 1


def test_vad_plan_must_cover_episode_contiguously() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        plan_alignment_windows(
            {"split_chunks": [_chunk(0, 59), _chunk(60, 120)]},
            duration_s=120,
            boundary_radius_s=12,
        )


def _chunk(start_s: float, end_s: float) -> dict[str, float]:
    return {"start_s": start_s, "end_s": end_s}


def _cue(cue_id: str, start_s: float, end_s: float) -> dict[str, object]:
    return {
        "cue_id": cue_id,
        "source_start_s": start_s,
        "source_end_s": end_s,
    }


def _result(cue_id: str, status: str, *, edge: float) -> dict[str, object]:
    return {
        **_cue(cue_id, 57, 61),
        "status": status,
        "score_signals": {
            "min_edge_distance_s": edge,
            "max_normalized_entropy": 0.5,
            "aligned_duration_s": 2.0,
        },
    }


def _window_result(
    index: int, start_s: float, end_s: float, cue: dict[str, object]
) -> dict[str, object]:
    return {
        "window_index": index,
        "window_kind": "core",
        "core_start_s": start_s,
        "core_end_s": end_s,
        "cues": [cue],
    }
