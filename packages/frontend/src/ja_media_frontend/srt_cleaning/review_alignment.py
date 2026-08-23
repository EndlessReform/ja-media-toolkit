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
    cues = [cue for window in results["windows"] for cue in window["cues"]]
    return {
        "source_sha256": case["cleaned_subtitle"]["source_sha256"],
        "results_path": results_path,
        "by_source_index": {
            int(cue["source_index"]): ReviewAlignment(
                start_s=float(cue["aligned_start_s"]),
                end_s=float(cue["aligned_end_s"]),
                status=str(cue["status"]),
                token_count=int(cue.get("token_count", 0)),
                score_signals=dict(cue.get("score_signals") or {}),
            )
            for cue in cues
        },
    }
