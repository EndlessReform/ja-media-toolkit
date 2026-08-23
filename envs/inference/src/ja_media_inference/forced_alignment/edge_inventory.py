"""Inventory saved candidate timings before running an edge-position experiment."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ja_media_inference.forced_alignment.stability_runner import (
    is_lexical_review_cue,
)


EDGE_BINS = (
    ("within_0_2s", 0.2),
    ("0_2_to_2s", 2.0),
    ("2_to_5s", 5.0),
    ("5_to_10s", 10.0),
    ("over_10s", float("inf")),
)


def load_slice_cases(slice_path: Path) -> dict[str, dict[str, Any]]:
    """Load prepared cases, input cues, and saved full-alignment results."""

    payload = json.loads(slice_path.read_text(encoding="utf-8"))
    if payload.get("schema_name") != "ja-media.forced-alignment.prepared-slice":
        raise ValueError(f"not a prepared alignment slice: {slice_path}")
    loaded = {}
    for item in payload["cases"]:
        case_name = str(item["case"])
        manifest_path = Path(item["case_manifest"])
        if not manifest_path.is_file():
            manifest_path = slice_path.parent / case_name / "case.json"
        case = json.loads(manifest_path.read_text(encoding="utf-8"))
        input_path = manifest_path.parent / case["cleaned_subtitle"]["input_cues"]
        records = [
            json.loads(line)
            for line in input_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        results_path = manifest_path.parent / "full-alignment" / "results.json"
        results = json.loads(results_path.read_text(encoding="utf-8"))
        loaded[case_name] = {
            "manifest_path": manifest_path,
            "case": case,
            "records": records,
            "results": results,
        }
    return loaded


def build_candidate_inventory(
    cases: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten every saved core/boundary candidate with its selection outcome."""

    rows = []
    for case_name, bundle in sorted(cases.items()):
        results = bundle["results"]
        records = {str(row["cue_id"]): row for row in bundle["records"]}
        selected = {
            str(row["cue_id"]): int(row["alignment_window_index"])
            for row in results["selected_cues"]
        }
        choices: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for window in results["windows"]:
            for cue in window["cues"]:
                choices.setdefault(str(cue["cue_id"]), []).append(
                    (int(window["window_index"]), cue)
                )
        for window in results["windows"]:
            cues = window["cues"]
            for position, cue in enumerate(cues):
                cue_id = str(cue["cue_id"])
                source = records[cue_id]
                signals = cue.get("score_signals") or {}
                alternatives = [
                    item
                    for item in choices[cue_id]
                    if item[0] != int(window["window_index"])
                ]
                rows.append(
                    {
                        "case": case_name,
                        "cue_id": cue_id,
                        "source_index": int(cue["source_index"]),
                        "text": str(cue["text"]),
                        "source_start_s": float(cue["source_start_s"]),
                        "source_end_s": float(cue["source_end_s"]),
                        "source_duration_s": float(cue["source_end_s"])
                        - float(cue["source_start_s"]),
                        "window_index": int(window["window_index"]),
                        "window_kind": str(window["window_kind"]),
                        "crop_start_s": float(window["crop_start_s"]),
                        "crop_end_s": float(window["crop_end_s"]),
                        "text_position": position,
                        "text_count": len(cues),
                        "nearest_text_edge": min(position, len(cues) - 1 - position),
                        "aligned_start_s": float(cue["aligned_start_s"]),
                        "aligned_end_s": float(cue["aligned_end_s"]),
                        "status": str(cue["status"]),
                        "ordered": cue["status"] == "aligned",
                        "edge_distance_s": float(
                            signals.get("min_edge_distance_s") or 0.0
                        ),
                        "reversed_token_count": int(
                            signals.get("reversed_token_count") or 0
                        ),
                        "backward_token_count": int(
                            signals.get("backward_token_count") or 0
                        ),
                        "zero_duration_token_count": int(
                            signals.get("zero_duration_token_count") or 0
                        ),
                        "selected": selected.get(cue_id)
                        == int(window["window_index"]),
                        "ordered_alternative_over_2s": any(
                            item[1]["status"] == "aligned"
                            and float(
                                item[1]["score_signals"]["min_edge_distance_s"]
                            )
                            > 2.0
                            for item in alternatives
                        ),
                        "strict_lexical_dialogue": is_lexical_review_cue(source),
                        "cleaning_decision": source.get("cleaning_decision"),
                        "cleaning_flags": list(source.get("flags") or []),
                        "cleaning_reasons": list(source.get("cleaning_reasons") or []),
                    }
                )
    return rows


def summarize_inventory(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count order failures by audio edge and text-list position."""

    return {
        "candidate_count": len(rows),
        "selected_count": sum(bool(row["selected"]) for row in rows),
        "selected_broken_count": sum(
            bool(row["selected"] and not row["ordered"]) for row in rows
        ),
        "selected_broken_with_ordered_interior_alternative": sum(
            bool(
                row["selected"]
                and not row["ordered"]
                and row["ordered_alternative_over_2s"]
            )
            for row in rows
        ),
        "all_candidates_by_audio_edge": _edge_summary(rows),
        "strict_lexical_candidates_by_audio_edge": _edge_summary(
            [row for row in rows if row["strict_lexical_dialogue"]]
        ),
        "strict_lexical_audio_and_text_edges": _crossed_summary(
            [row for row in rows if row["strict_lexical_dialogue"]]
        ),
    }


def write_inventory(
    rows: list[dict[str, Any]], output_dir: Path
) -> tuple[Path, Path, Path]:
    """Write row-level JSON/TSV plus compact aggregate metrics."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "edge-inventory.json"
    tsv_path = output_dir / "edge-inventory.tsv"
    summary_path = output_dir / "edge-summary.json"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    with tsv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "cleaning_flags": json.dumps(row["cleaning_flags"], ensure_ascii=False),
                    "cleaning_reasons": json.dumps(
                        row["cleaning_reasons"], ensure_ascii=False
                    ),
                }
            )
    summary_path.write_text(
        json.dumps(summarize_inventory(rows), ensure_ascii=False, indent=2) + "\n"
    )
    return json_path, tsv_path, summary_path


def _edge_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lower = 0.0
    summary = []
    for name, upper in EDGE_BINS:
        members = [
            row
            for row in rows
            if lower < float(row["edge_distance_s"]) <= upper
            or (lower == 0 and float(row["edge_distance_s"]) == 0)
        ]
        broken = sum(not row["ordered"] for row in members)
        summary.append(
            {
                "bin": name,
                "candidate_count": len(members),
                "broken_count": broken,
                "broken_ratio": broken / len(members) if members else None,
            }
        )
        lower = upper
    return summary


def _crossed_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for audio_near in (False, True):
        for text_near in (False, True):
            members = [
                row
                for row in rows
                if (float(row["edge_distance_s"]) <= 2) == audio_near
                and (int(row["nearest_text_edge"]) <= 1) == text_near
            ]
            broken = sum(not row["ordered"] for row in members)
            summary.append(
                {
                    "audio_within_2s": audio_near,
                    "first_or_last_two_text_cues": text_near,
                    "candidate_count": len(members),
                    "broken_count": broken,
                    "broken_ratio": broken / len(members) if members else None,
                }
            )
    return summary
