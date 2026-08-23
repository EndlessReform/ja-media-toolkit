"""Checks for the crop-position stability experiment."""

import json
from pathlib import Path

import pytest

from ja_media_inference.forced_alignment import stability_runner
from ja_media_inference.forced_alignment.stability_runner import (
    build_blind_pairs,
    is_lexical_review_cue,
    select_lexical_targets,
    summarize_stability,
)
from ja_media_inference.forced_alignment.stress_runner import suggest_concurrency
from ja_media_inference.forced_alignment.text_units import TokenAlignment


def test_lexical_selection_excludes_sfx_music_flags_and_short_noise() -> None:
    dialogue = _record(1, "今日は学校に行きます")

    assert is_lexical_review_cue(dialogue)
    assert not is_lexical_review_cue(_record(2, "ドア", flags=["very_short_kana"]))
    assert not is_lexical_review_cue(_record(3, "♪今日は学校に行きます♪"))
    assert not is_lexical_review_cue(
        _record(4, "今日は学校に行きます", decision="edit")
    )
    assert not is_lexical_review_cue(_record(5, "（爆発音）"))


def test_selection_is_even_and_fits_all_crop_positions() -> None:
    records = [_record(index, f"これは普通の台詞です{index}") for index in range(1, 11)]

    selected = select_lexical_targets(
        records,
        duration_s=600,
        sample_count=3,
        max_window_s=180,
    )

    assert [row["source_index"] for row in selected] == [6, 8, 10]


def test_summary_measures_within_cue_movement_not_source_delta() -> None:
    results = [
        _arm("cue:1", 1, 60, "beginning", 10.0, 11.0),
        _arm("cue:1", 1, 60, "middle", 10.08, 11.08),
        _arm("cue:1", 1, 60, "end", 10.16, 11.16),
        _arm("cue:1", 1, 180, "beginning", 9.0, 12.0, status="suspicious"),
        _arm("cue:1", 1, 180, "middle", 10.0, 11.0),
        _arm("cue:1", 1, 180, "end", 11.0, 12.0),
    ]

    summary = summarize_stability(results)

    assert summary[0]["median_max_border_range_s"] == pytest.approx(0.16)
    assert summary[0]["within_0_16_s_count"] == 1
    assert summary[1]["median_max_border_range_s"] == 2.0
    assert summary[1]["broken_order_arm_count"] == 1


def test_blind_pairs_use_only_middle_arms_and_hide_duration_behind_labels() -> None:
    results = [
        _arm("cue:1", 1, 60, "beginning", 9.0, 10.0),
        _arm("cue:1", 1, 60, "middle", 10.0, 11.0),
        _arm("cue:1", 1, 180, "middle", 10.1, 11.1),
    ]

    pair = build_blind_pairs(results)[0]

    assert set(pair["candidates"]) == {"A", "B"}
    assert {item["window_size_s"] for item in pair["candidates"].values()} == {
        60,
        180,
    }
    assert pair["text"] == "これは普通の台詞です"


def test_stress_suggestion_picks_smallest_level_near_peak_throughput() -> None:
    measurements = [
        {"concurrency": 1, "requests_per_s": 1.0, "failed_requests": 0},
        {"concurrency": 8, "requests_per_s": 7.5, "failed_requests": 0},
        {"concurrency": 16, "requests_per_s": 9.2, "failed_requests": 0},
        {"concurrency": 32, "requests_per_s": 10.0, "failed_requests": 0},
        {"concurrency": 64, "requests_per_s": 0.0, "failed_requests": 2},
    ]

    assert suggest_concurrency(measurements) == 16


def test_stability_runner_writes_a_new_result_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record(7, "これは普通の台詞です")
    record["cleaned_index"] = 7
    input_cues = tmp_path / "input-cues.jsonl"
    input_cues.write_text(json.dumps(record, ensure_ascii=False) + "\n")
    manifest = tmp_path / "case.json"
    manifest.write_text(
        json.dumps(
            {
                "case": {"series": "test", "episode": 1},
                "audio": {"duration_s": 500},
                "cleaned_subtitle": {
                    "input_cues": input_cues.name,
                    "source_sha256": "a" * 64,
                },
            }
        )
    )
    monkeypatch.setattr(stability_runner, "Qwen3AdapterClient", FixedAdapter)

    destination = stability_runner.run_stability_case(
        manifest,
        base_url="http://adapter",
        sample_count=1,
        concurrency=2,
    )

    assert destination == tmp_path / "stability" / "results.json"
    assert len(json.loads(destination.read_text())["results"]) == 6


def _record(
    index: int,
    text: str,
    *,
    flags: list[str] | None = None,
    decision: str = "as_is",
) -> dict:
    start_s = float(index * 30)
    return {
        "cue_id": f"cue:{index}",
        "source_index": index,
        "source_start_s": start_s,
        "source_end_s": start_s + 2,
        "alignment_text": text,
        "cleaning_decision": decision,
        "cleaning_reasons": [],
        "flags": flags or [],
    }


def _arm(
    cue_id: str,
    source_index: int,
    window_s: float,
    position: str,
    start_s: float,
    end_s: float,
    *,
    status: str = "aligned",
) -> dict:
    return {
        "target_cue_id": cue_id,
        "window_size_s": window_s,
        "position_name": position,
        "target_result": {
            "source_index": source_index,
            "text": "これは普通の台詞です",
            "aligned_start_s": start_s,
            "aligned_end_s": end_s,
            "status": status,
            "score_signals": {"min_edge_distance_s": 2.0},
        },
    }


class FixedAdapter:
    name = "fixed-adapter"
    model = "fixed-model"

    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url

    def cache_audio(self, _audio) -> str:  # type: ignore[no-untyped-def]
        return "a" * 64

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
