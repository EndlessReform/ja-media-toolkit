"""Choose reviewed failures, matched controls, and selector conflicts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ja_media_inference.forced_alignment.edge_inventory import load_slice_cases
from ja_media_inference.forced_alignment.stability_runner import (
    is_lexical_review_cue,
)


def build_edge_targets(
    slice_path: Path,
    reviewed_targets_path: Path,
    *,
    cohort_size: int = 12,
    max_window_s: float = 180.0,
    edge_clearance_s: float = 2.0,
) -> dict[str, Any]:
    """Build three equally sized, input-pinned target cohorts."""

    cases = load_slice_cases(slice_path)
    reviewed = json.loads(reviewed_targets_path.read_text(encoding="utf-8"))
    broken = []
    for item in reviewed["targets"]:
        case_name = str(item["case"])
        source_index = int(item["source_index"])
        reviewed_candidate = _interior_broken_candidate(
            cases[case_name], source_index
        )
        broken.append(
            _target_from_source(cases[case_name], source_index)
            | {
                "case": case_name,
                "cohort": "reviewed_interior_broken",
                "saved_status": reviewed_candidate["status"],
                "saved_edge_distance_s": reviewed_candidate["score_signals"][
                    "min_edge_distance_s"
                ],
                "reviewed_window_index": reviewed_candidate["window_index"],
            }
        )
    if len(broken) != cohort_size:
        raise ValueError(
            f"reviewed target count is {len(broken)}; expected {cohort_size}"
        )
    _validate_targets(
        cases,
        broken,
        max_window_s=max_window_s,
        edge_clearance_s=edge_clearance_s,
    )
    controls = _matched_controls(
        cases,
        broken,
        max_window_s=max_window_s,
        edge_clearance_s=edge_clearance_s,
    )
    conflicts = _selector_conflicts(
        cases,
        count=cohort_size,
        excluded={(row["case"], row["cue_id"]) for row in broken + controls},
        max_window_s=max_window_s,
        edge_clearance_s=edge_clearance_s,
    )
    targets = broken + controls + conflicts
    return {
        "schema_name": "ja-media.forced-alignment.edge-targets",
        "schema_version": "1.0.0",
        "slice": str(slice_path),
        "reviewed_targets": str(reviewed_targets_path),
        "cohort_size": cohort_size,
        "window_sizes_s": [60.0, 180.0],
        "positions": ["beginning", "middle", "end"],
        "edge_clearance_s": edge_clearance_s,
        "request_count": len(targets) * 6,
        "targets": targets,
    }


def write_edge_targets(payload: dict[str, Any], output_dir: Path) -> Path:
    """Write the exact 36-cue experiment input."""

    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "targets.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return destination


def _target_from_source(bundle: dict[str, Any], source_index: int) -> dict[str, Any]:
    record = next(
        (row for row in bundle["records"] if int(row["source_index"]) == source_index),
        None,
    )
    if record is None:
        raise ValueError(f"source index {source_index} not found")
    selected = next(
        row
        for row in bundle["results"]["selected_cues"]
        if int(row["source_index"]) == source_index
    )
    return {
        "cue_id": str(record["cue_id"]),
        "source_index": source_index,
        "text": str(record["alignment_text"]),
        "source_start_s": float(record["source_start_s"]),
        "source_end_s": float(record["source_end_s"]),
        "saved_status": str(selected["status"]),
        "saved_edge_distance_s": float(
            selected["score_signals"]["min_edge_distance_s"]
        ),
    }


def _interior_broken_candidate(
    bundle: dict[str, Any], source_index: int
) -> dict[str, Any]:
    candidates = [
        cue | {"window_index": int(window["window_index"])}
        for window in bundle["results"]["windows"]
        for cue in window["cues"]
        if int(cue["source_index"]) == source_index
        and cue["status"] != "aligned"
        and float(cue["score_signals"]["min_edge_distance_s"]) > 10
    ]
    if not candidates:
        raise ValueError(f"source index {source_index} has no reviewed interior failure")
    return max(
        candidates,
        key=lambda cue: float(cue["score_signals"]["min_edge_distance_s"]),
    )


def _matched_controls(
    cases: dict[str, dict[str, Any]],
    broken: list[dict[str, Any]],
    *,
    max_window_s: float,
    edge_clearance_s: float,
) -> list[dict[str, Any]]:
    used: set[tuple[str, str]] = set()
    controls = []
    for target in broken:
        candidates = _clean_selected_candidates(
            cases,
            max_window_s=max_window_s,
            edge_clearance_s=edge_clearance_s,
        )
        candidates = [
            row
            for row in candidates
            if (row["case"], row["cue_id"]) not in used
            and row["cue_id"] != target["cue_id"]
        ]
        same_case = [row for row in candidates if row["case"] == target["case"]]
        pool = same_case or candidates
        winner = min(pool, key=lambda row: _match_cost(target, row))
        winner["cohort"] = "matched_interior_aligned"
        winner["matched_to"] = target["cue_id"]
        controls.append(winner)
        used.add((winner["case"], winner["cue_id"]))
    return controls


def _clean_selected_candidates(
    cases: dict[str, dict[str, Any]],
    *,
    max_window_s: float,
    edge_clearance_s: float,
) -> list[dict[str, Any]]:
    candidates = []
    for case_name, bundle in cases.items():
        records = {str(row["cue_id"]): row for row in bundle["records"]}
        duration_s = float(bundle["case"]["audio"]["duration_s"])
        for selected in bundle["results"]["selected_cues"]:
            record = records[str(selected["cue_id"])]
            if (
                selected["status"] == "aligned"
                and float(selected["score_signals"]["min_edge_distance_s"]) > 10
                and is_lexical_review_cue(record)
                and _fits(record, duration_s, max_window_s, edge_clearance_s)
            ):
                candidates.append(
                    _target_from_source(bundle, int(record["source_index"]))
                    | {"case": case_name}
                )
    return candidates


def _selector_conflicts(
    cases: dict[str, dict[str, Any]],
    *,
    count: int,
    excluded: set[tuple[str, str]],
    max_window_s: float,
    edge_clearance_s: float,
) -> list[dict[str, Any]]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for case_name, bundle in cases.items():
        records = {str(row["cue_id"]): row for row in bundle["records"]}
        duration_s = float(bundle["case"]["audio"]["duration_s"])
        choices: dict[str, list[dict[str, Any]]] = {}
        for window in bundle["results"]["windows"]:
            for cue in window["cues"]:
                choices.setdefault(str(cue["cue_id"]), []).append(cue)
        for selected in bundle["results"]["selected_cues"]:
            cue_id = str(selected["cue_id"])
            record = records[cue_id]
            has_ordered_interior = any(
                cue["status"] == "aligned"
                and float(cue["score_signals"]["min_edge_distance_s"]) > 2
                for cue in choices[cue_id]
            )
            if (
                selected["status"] != "aligned"
                and has_ordered_interior
                and is_lexical_review_cue(record)
                and _fits(record, duration_s, max_window_s, edge_clearance_s)
                and (case_name, cue_id) not in excluded
            ):
                by_case.setdefault(case_name, []).append(
                    _target_from_source(bundle, int(record["source_index"]))
                    | {"case": case_name, "cohort": "selector_conflict"}
                )
    mandatory_case = "anilist-71-e01-1ca403895485"
    mandatory = next(
        row for row in by_case[mandatory_case] if int(row["source_index"]) == 81
    )
    selected = [mandatory]
    for case_name in sorted(by_case):
        if len(selected) == count:
            break
        candidate = min(by_case[case_name], key=lambda row: int(row["source_index"]))
        if candidate["cue_id"] != mandatory["cue_id"]:
            selected.append(candidate)
    if len(selected) != count:
        raise ValueError(f"only {len(selected)} selector-conflict cases were available")
    return selected


def _validate_targets(
    cases: dict[str, dict[str, Any]],
    targets: list[dict[str, Any]],
    *,
    max_window_s: float,
    edge_clearance_s: float,
) -> None:
    for target in targets:
        bundle = cases[target["case"]]
        record = next(
            row for row in bundle["records"] if row["cue_id"] == target["cue_id"]
        )
        duration_s = float(bundle["case"]["audio"]["duration_s"])
        if not is_lexical_review_cue(record):
            raise ValueError(f"reviewed target is not strict dialogue: {target['cue_id']}")
        if not _fits(record, duration_s, max_window_s, edge_clearance_s):
            raise ValueError(f"reviewed target does not fit all crops: {target['cue_id']}")


def _fits(
    record: dict[str, Any], duration_s: float, window_s: float, clearance_s: float
) -> bool:
    return (
        float(record["source_start_s"]) - clearance_s >= 0
        and float(record["source_start_s"]) - clearance_s + window_s <= duration_s
        and float(record["source_end_s"]) + clearance_s - window_s >= 0
        and float(record["source_end_s"]) + clearance_s <= duration_s
    )


def _match_cost(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, int]:
    left_duration = float(left["source_end_s"]) - float(left["source_start_s"])
    right_duration = float(right["source_end_s"]) - float(right["source_start_s"])
    return (
        abs(left_duration - right_duration)
        + abs(len(str(left["text"])) - len(str(right["text"]))) * 0.1,
        int(right["source_index"]),
    )
