"""Measure whether crop duration and cue position move forced-alignment borders."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
from typing import Any

from ja_media_inference.forced_alignment.case_runner import (
    _midpoint,
    _read_jsonl,
)
from ja_media_inference.forced_alignment.qwen3_adapter_client import Qwen3AdapterClient
from ja_media_inference.forced_alignment.stability_placements import (
    EDGE_POSITION_NAMES,
    compare_positions,
    edge_clearance_crop,
)
from ja_media_inference.forced_alignment.stability_reporting import (
    build_blind_pairs,
    summarize_stability,
)
from ja_media_inference.forced_alignment.window_execution import align_remote_window


DEFAULT_WINDOW_SIZES_S = (60.0, 180.0)
DEFAULT_POSITIONS = (("beginning", 0.15), ("middle", 0.5), ("end", 0.85))
_JAPANESE_LEXICAL = re.compile(r"[一-龯々〆ヵヶぁ-ゖァ-ヺー]")
_MUSIC_GLYPHS = frozenset("♪♫♬♩♭♯")


def run_stability_case(
    case_manifest: Path,
    *,
    base_url: str,
    sample_count: int = 6,
    window_sizes_s: tuple[float, ...] = DEFAULT_WINDOW_SIZES_S,
    positions: tuple[tuple[str, float], ...] = DEFAULT_POSITIONS,
    text_field: str = "alignment_text",
    concurrency: int = 1,
    targets: list[dict[str, Any]] | None = None,
    edge_clearance_s: float | None = None,
) -> Path:
    """Run the same lexical cues at three positions in each requested duration."""

    case_root = case_manifest.parent
    case = json.loads(case_manifest.read_text(encoding="utf-8"))
    duration_s = float(case["audio"]["duration_s"])
    records = _read_jsonl(case_root / case["cleaned_subtitle"]["input_cues"])
    targets = targets or select_lexical_targets(
        records,
        duration_s=duration_s,
        sample_count=sample_count,
        max_window_s=max(window_sizes_s),
        positions=positions,
    )
    if edge_clearance_s is not None:
        positions = tuple((name, 0.5) for name in EDGE_POSITION_NAMES)
    aligner = Qwen3AdapterClient(base_url=base_url)
    audio_id = aligner.cache_audio(case["audio"])
    output_root = case_root / "stability"
    output_root.mkdir(parents=True, exist_ok=True)
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    jobs = [
        (target, window_s, position_name, position)
        for target in targets
        for window_s in window_sizes_s
        for position_name, position in positions
    ]

    def run_job(job: tuple[dict[str, Any], float, str, float]) -> dict[str, Any]:
        target, window_s, position_name, position = job
        return _run_placement(
            aligner,
            audio_id,
            records,
            target,
            duration_s=duration_s,
            window_s=window_s,
            position_name=position_name,
            position=position,
            text_field=text_field,
            edge_clearance_s=edge_clearance_s,
        )

    # Warm the server-side tokenizer/config caches before concurrent requests.
    results = [run_job(jobs[0])]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results.extend(executor.map(run_job, jobs[1:]))
    report = {
        "schema_name": "ja-media.forced-alignment.stability",
        "schema_version": "1.0.0",
        "case": case["case"],
        "source_sha256": case["cleaned_subtitle"]["source_sha256"],
        "backend": {"type": aligner.name, "model": aligner.model},
        "client_concurrency": concurrency,
        "selection_policy": {
            "name": "lexical-dialogue-v1",
            "sample_count": len(targets),
            "excludes": [
                "removed or edited cleaning decisions",
                "flagged cleaning inputs",
                "music glyphs",
                "fewer than four Japanese lexical characters",
                "source spans outside 0.5-8.0 seconds",
            ],
        },
        "window_sizes_s": list(window_sizes_s),
        "positions": [
            {"name": name, "fraction": fraction} for name, fraction in positions
        ],
        "placement_policy": (
            {
                "name": "explicit-source-cue-edge-clearance-v1",
                "edge_clearance_s": edge_clearance_s,
            }
            if edge_clearance_s is not None
            else {"name": "fractional-cue-midpoint-v1"}
        ),
        "targets": targets,
        "results": results,
        "stability_summary": summarize_stability(results),
        "position_comparisons": compare_positions(results),
        "blind_pairs": build_blind_pairs(results),
    }
    destination = output_root / "results.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return destination


def select_lexical_targets(
    records: list[dict[str, Any]],
    *,
    duration_s: float,
    sample_count: int,
    max_window_s: float,
    positions: tuple[tuple[str, float], ...] = DEFAULT_POSITIONS,
) -> list[dict[str, Any]]:
    """Select evenly distributed, unflagged dialogue cues that fit every placement."""

    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    earliest = max(max_window_s * fraction for _name, fraction in positions)
    latest = duration_s - max(
        max_window_s * (1.0 - fraction) for _name, fraction in positions
    )
    candidates = [
        row
        for row in records
        if earliest <= _midpoint(row) <= latest and is_lexical_review_cue(row)
    ]
    if len(candidates) < sample_count:
        raise ValueError(
            f"only {len(candidates)} lexical cues fit all stability placements; "
            f"requested {sample_count}"
        )
    indexes = [
        round(index * (len(candidates) - 1) / max(1, sample_count - 1))
        for index in range(sample_count)
    ]
    return [candidates[index] for index in indexes]


def is_lexical_review_cue(row: dict[str, Any]) -> bool:
    """Keep ordinary retained dialogue out of the SFX/lyrics review path."""

    text = str(row.get("alignment_text") or "").strip()
    duration_s = float(row["source_end_s"]) - float(row["source_start_s"])
    return (
        row.get("cleaning_decision") in {"as_is", "asis"}
        and not row.get("flags")
        and not row.get("cleaning_reasons")
        and 0.5 <= duration_s <= 8.0
        and not any(glyph in text for glyph in _MUSIC_GLYPHS)
        and len(_JAPANESE_LEXICAL.findall(text)) >= 4
    )


def _run_placement(
    aligner: Qwen3AdapterClient,
    audio_id: str,
    records: list[dict[str, Any]],
    target: dict[str, Any],
    *,
    duration_s: float,
    window_s: float,
    position_name: str,
    position: float,
    text_field: str,
    edge_clearance_s: float | None = None,
) -> dict[str, Any]:
    if edge_clearance_s is None:
        center_s = _midpoint(target)
        crop_start_s = center_s - position * window_s
        crop_end_s = crop_start_s + window_s
        if crop_start_s < -1e-6 or crop_end_s > duration_s + 1e-6:
            raise ValueError(
                f"target {target['cue_id']} does not fit {window_s}s/{position_name}: "
                f"crop={crop_start_s:.6f}-{crop_end_s:.6f}, "
                f"audio=0-{duration_s:.6f}"
            )
        crop_start_s = max(0.0, crop_start_s)
        crop_end_s = min(duration_s, crop_end_s)
    else:
        crop_start_s, crop_end_s = edge_clearance_crop(
            target,
            duration_s=duration_s,
            window_s=window_s,
            position_name=position_name,
            edge_clearance_s=edge_clearance_s,
        )
    members = [row for row in records if crop_start_s <= _midpoint(row) < crop_end_s]
    result = align_remote_window(
        aligner,
        audio_id,
        members,
        target_id=str(target["cue_id"]),
        target_name=f"cue-{target['source_index']}-{position_name}",
        crop_start_s=crop_start_s,
        crop_end_s=crop_end_s,
        text_field=text_field,
    )
    return {key: value for key, value in result.items() if key != "cues"} | {
        "window_size_s": window_s,
        "position_name": position_name,
        "position": position,
    }
