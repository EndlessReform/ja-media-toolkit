"""Checks for the crop-edge inventory and campaign orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ja_media_inference.forced_alignment import edge_campaign
from ja_media_inference.forced_alignment.edge_inventory import (
    build_candidate_inventory,
    summarize_inventory,
)


def test_inventory_keeps_candidate_selection_and_ordered_alternative() -> None:
    bundle = _bundle(Path("case.json"))

    rows = build_candidate_inventory({"case-a": bundle})

    assert len(rows) == 2
    selected = next(row for row in rows if row["selected"])
    assert not selected["ordered"]
    assert selected["ordered_alternative_over_2s"]
    summary = summarize_inventory(rows)
    assert summary["selected_broken_count"] == 1
    assert summary["selected_broken_with_ordered_interior_alternative"] == 1


def test_campaign_runs_every_placement_and_writes_f7_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_root = tmp_path / "case-a"
    case_root.mkdir()
    manifest_path = case_root / "case.json"
    bundle = _bundle(manifest_path)
    slice_path = tmp_path / "slice.json"
    slice_path.write_text("{}")
    target = {
        "case": "case-a",
        "cue_id": "cue:7",
        "source_index": 7,
        "text": "これは普通の台詞です",
        "source_start_s": 250.0,
        "source_end_s": 252.0,
        "cohort": "reviewed_interior_broken",
    }
    targets_path = tmp_path / "edge-experiment" / "targets.json"
    targets_path.parent.mkdir()
    targets_path.write_text(
        json.dumps(
            {
                "window_sizes_s": [60.0, 180.0],
                "positions": ["beginning", "middle", "end"],
                "edge_clearance_s": 2.0,
                "targets": [target],
            }
        )
    )
    monkeypatch.setattr(
        edge_campaign, "load_slice_cases", lambda _path: {"case-a": bundle}
    )
    monkeypatch.setattr(edge_campaign, "Qwen3AdapterClient", FixedAdapter)
    monkeypatch.setattr(edge_campaign, "_run_placement", _fixed_placement)

    destination = edge_campaign.run_edge_campaign(
        slice_path,
        targets_path,
        base_url="http://adapter",
        concurrency=4,
    )

    aggregate = json.loads(destination.read_text())
    assert aggregate["request_count"] == 6
    assert len(aggregate["position_comparisons"]) == 4
    report_path = case_root / "stability" / "results.json"
    report = json.loads(report_path.read_text())
    assert report["experiment_kind"] == "explicit-edge-position"
    assert len(report["results"]) == 6


def _bundle(manifest_path: Path) -> dict:
    record = {
        "cue_id": "cue:7",
        "source_index": 7,
        "source_start_s": 250.0,
        "source_end_s": 252.0,
        "alignment_text": "これは普通の台詞です",
        "cleaning_decision": "as_is",
        "cleaning_reasons": [],
        "flags": [],
    }
    broken = _candidate("suspicious", 0.1)
    aligned = _candidate("aligned", 5.0)
    return {
        "manifest_path": manifest_path,
        "case": {
            "audio": {"duration_s": 600.0},
            "cleaned_subtitle": {"source_sha256": "a" * 64},
        },
        "records": [record],
        "results": {
            "selected_cues": [broken | {"alignment_window_index": 1}],
            "windows": [
                {
                    "window_index": 1,
                    "window_kind": "main",
                    "crop_start_s": 200.0,
                    "crop_end_s": 320.0,
                    "cues": [broken],
                },
                {
                    "window_index": 2,
                    "window_kind": "boundary",
                    "crop_start_s": 220.0,
                    "crop_end_s": 280.0,
                    "cues": [aligned],
                },
            ],
        },
    }


def _candidate(status: str, edge_s: float) -> dict:
    return {
        "cue_id": "cue:7",
        "source_index": 7,
        "text": "これは普通の台詞です",
        "source_start_s": 250.0,
        "source_end_s": 252.0,
        "aligned_start_s": 250.1,
        "aligned_end_s": 251.9,
        "status": status,
        "score_signals": {
            "min_edge_distance_s": edge_s,
            "reversed_token_count": 1 if status != "aligned" else 0,
        },
    }


class FixedAdapter:
    name = "fixed-adapter"
    model = "fixed-model"

    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url

    def cache_audio(self, _audio) -> str:  # type: ignore[no-untyped-def]
        return "a" * 64


def _fixed_placement(
    _aligner,
    _audio_id,
    _records,
    target,
    **options,  # type: ignore[no-untyped-def]
) -> dict:
    position = options["position_name"]
    offset = {"beginning": -0.5, "middle": 0.0, "end": 0.25}[position]
    return {
        "target_cue_id": target["cue_id"],
        "window_size_s": options["window_s"],
        "position_name": position,
        "crop_start_s": 0.0,
        "crop_end_s": 1000.0,
        "target_result": {
            "source_index": target["source_index"],
            "text": target["alignment_text"],
            "aligned_start_s": 250.0 + offset,
            "aligned_end_s": 252.0 + offset,
            "status": "suspicious" if position == "beginning" else "aligned",
            "score_signals": {
                "min_edge_distance_s": 0.1 if position == "beginning" else 2.0
            },
        },
    }
