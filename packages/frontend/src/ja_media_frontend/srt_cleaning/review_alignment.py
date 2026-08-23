"""Load forced-alignment results for the subtitle cleaning review UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ja_media_frontend.srt_cleaning.review_models import ReviewAlignment


def read_alignment_case(case_path: Path | None) -> dict[str, Any] | None:
    """Load candidate timings linked by original source cue index."""

    if case_path is None:
        return None
    case_path = case_path.expanduser().resolve()
    case = json.loads(case_path.read_text(encoding="utf-8"))
    results_path = case_path.parent / "full-alignment" / "results.json"
    if not results_path.is_file():
        raise FileNotFoundError(f"Missing full alignment results: {results_path}")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    audio_path = case_path.parent / case["audio"]["relative_path"]
    if not audio_path.is_file():
        raise FileNotFoundError(f"Missing prepared alignment audio: {audio_path}")
    cues = results.get("selected_cues") or [
        cue for window in results["windows"] for cue in window["cues"]
    ]
    return {
        "source_subtitle_id": case["cleaned_subtitle"]["source_subtitle_id"],
        "source_sha256": case["cleaned_subtitle"]["source_sha256"],
        "results_path": results_path,
        "audio_path": audio_path,
        "by_source_index": {
            int(cue["source_index"]): ReviewAlignment(
                start_s=float(cue["aligned_start_s"]),
                end_s=float(cue["aligned_end_s"]),
                status=str(cue["status"]),
                token_count=int(cue.get("token_count", 0)),
                score_signals=dict(cue.get("score_signals") or {}),
                window_index=(
                    int(cue["alignment_window_index"])
                    if cue.get("alignment_window_index") is not None
                    else None
                ),
                window_kind=cue.get("alignment_window_kind"),
                candidate_count=int(cue.get("alignment_candidate_count", 1)),
            )
            for cue in cues
        },
    }


def read_alignment_cases(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    """Load one case or every case named by a prepared slice manifest."""

    if path is None:
        return {}
    resolved = path.expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if payload.get("schema_name") == "ja-media.forced-alignment.prepared-slice":
        case_paths = [Path(row["case_manifest"]) for row in payload["cases"]]
    else:
        case_paths = [resolved]
    loaded = [read_alignment_case(case_path) for case_path in case_paths]
    alignments = {
        (item["source_subtitle_id"], item["source_sha256"]): item
        for item in loaded
        if item is not None
    }
    if len(alignments) != len(loaded):
        raise ValueError("alignment slice contains duplicate source identities")
    return alignments
