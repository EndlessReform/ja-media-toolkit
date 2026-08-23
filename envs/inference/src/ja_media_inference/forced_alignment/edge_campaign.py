"""Run all edge-position requests across one prepared subtitle slice."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import Any

from ja_media_inference.forced_alignment.edge_inventory import load_slice_cases
from ja_media_inference.forced_alignment.qwen3_adapter_client import Qwen3AdapterClient
from ja_media_inference.forced_alignment.stability_placements import (
    compare_positions,
)
from ja_media_inference.forced_alignment.stability_runner import (
    _run_placement,
    build_blind_pairs,
    summarize_stability,
)


def run_edge_campaign(
    slice_path: Path,
    targets_path: Path,
    *,
    base_url: str,
    concurrency: int = 32,
    text_field: str = "alignment_text",
) -> Path:
    """Submit every case/target/position request through one shared executor."""

    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    cases = load_slice_cases(slice_path)
    target_payload = json.loads(targets_path.read_text(encoding="utf-8"))
    targets = target_payload["targets"]
    window_sizes_s = tuple(float(value) for value in target_payload["window_sizes_s"])
    positions = tuple(str(value) for value in target_payload["positions"])
    edge_clearance_s = float(target_payload["edge_clearance_s"])
    aligner = Qwen3AdapterClient(base_url=base_url)
    audio_ids = {
        case_name: aligner.cache_audio(bundle["case"]["audio"])
        for case_name, bundle in cases.items()
        if any(target["case"] == case_name for target in targets)
    }
    records_by_case = {
        case_name: {str(row["cue_id"]): row for row in bundle["records"]}
        for case_name, bundle in cases.items()
    }
    jobs = [
        (target, window_s, position_name)
        for target in targets
        for window_s in window_sizes_s
        for position_name in positions
    ]

    def run_job(job: tuple[dict[str, Any], float, str]) -> dict[str, Any]:
        target_spec, window_s, position_name = job
        case_name = str(target_spec["case"])
        bundle = cases[case_name]
        record = records_by_case[case_name][str(target_spec["cue_id"])]
        result = _run_placement(
            aligner,
            audio_ids[case_name],
            bundle["records"],
            record,
            duration_s=float(bundle["case"]["audio"]["duration_s"]),
            window_s=window_s,
            position_name=position_name,
            position=0.5,
            text_field=text_field,
            edge_clearance_s=edge_clearance_s,
        )
        return {
            **result,
            "case": case_name,
            "cohort": target_spec["cohort"],
        }

    results = [run_job(jobs[0])]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results.extend(executor.map(run_job, jobs[1:]))

    output_dir = targets_path.parent
    report = {
        "schema_name": "ja-media.forced-alignment.edge-campaign",
        "schema_version": "1.0.0",
        "slice": str(slice_path),
        "targets": str(targets_path),
        "backend": {"type": aligner.name, "model": aligner.model},
        "client_concurrency": concurrency,
        "request_count": len(results),
        "window_sizes_s": list(window_sizes_s),
        "positions": list(positions),
        "edge_clearance_s": edge_clearance_s,
        "results": results,
        "position_comparisons": compare_positions(results),
        "cohort_summary": _cohort_summary(results),
    }
    destination = output_dir / "results.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _write_case_reports(cases, targets, results, target_payload, concurrency)
    return destination


def _write_case_reports(
    cases: dict[str, dict[str, Any]],
    targets: list[dict[str, Any]],
    results: list[dict[str, Any]],
    target_payload: dict[str, Any],
    concurrency: int,
) -> None:
    for case_name in sorted({str(row["case"]) for row in targets}):
        bundle = cases[case_name]
        case_targets = [row for row in targets if row["case"] == case_name]
        case_results = [row for row in results if row["case"] == case_name]
        report = {
            "schema_name": "ja-media.forced-alignment.stability",
            "schema_version": "1.0.0",
            "experiment_kind": "explicit-edge-position",
            "case": case_name,
            "source_sha256": bundle["case"]["cleaned_subtitle"]["source_sha256"],
            "client_concurrency": concurrency,
            "selection_policy": {
                "name": "reviewed-edge-cohorts-v1",
                "cohorts": sorted({str(row["cohort"]) for row in case_targets}),
            },
            "window_sizes_s": target_payload["window_sizes_s"],
            "positions": target_payload["positions"],
            "placement_policy": {
                "name": "explicit-source-cue-edge-clearance-v1",
                "edge_clearance_s": target_payload["edge_clearance_s"],
            },
            "targets": case_targets,
            "results": case_results,
            "stability_summary": summarize_stability(case_results),
            "position_comparisons": compare_positions(case_results),
            "blind_pairs": build_blind_pairs(case_results),
        }
        destination = bundle["manifest_path"].parent / "stability" / "results.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def _cohort_summary(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for cohort in sorted({str(row["cohort"]) for row in results}):
        members = [row for row in results if row["cohort"] == cohort]
        comparisons = compare_positions(members)
        summary.append(
            {
                "cohort": cohort,
                "target_count": len({str(row["target_cue_id"]) for row in members}),
                "request_count": len(members),
                "broken_request_count": sum(
                    row["target_result"]["status"] != "aligned" for row in members
                ),
                "became_broken_at_edge_count": sum(
                    row["became_broken_at_edge"] for row in comparisons
                ),
                "edge_damaged_count": sum(row["edge_damaged"] for row in comparisons),
                "clamped_to_audio_edge_count": sum(
                    row["clamped_to_audio_edge"] for row in comparisons
                ),
                "outside_audio_crop_request_count": sum(
                    float(row["target_result"]["aligned_start_s"])
                    < float(row["crop_start_s"]) - 0.01
                    or float(row["target_result"]["aligned_end_s"])
                    > float(row["crop_end_s"]) + 0.01
                    for row in members
                ),
            }
        )
    return summary
