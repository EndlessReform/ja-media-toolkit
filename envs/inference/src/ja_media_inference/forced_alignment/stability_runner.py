"""Measure whether crop duration and cue position move forced-alignment borders."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
from statistics import median
from typing import Any, Iterable

from ja_media_inference.forced_alignment.case_runner import (
    _midpoint,
    _read_jsonl,
)
from ja_media_inference.forced_alignment.qwen3_adapter_client import Qwen3AdapterClient
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
) -> Path:
    """Run the same lexical cues at three positions in each requested duration."""

    case_root = case_manifest.parent
    case = json.loads(case_manifest.read_text(encoding="utf-8"))
    duration_s = float(case["audio"]["duration_s"])
    records = _read_jsonl(case_root / case["cleaned_subtitle"]["input_cues"])
    targets = select_lexical_targets(
        records,
        duration_s=duration_s,
        sample_count=sample_count,
        max_window_s=max(window_sizes_s),
        positions=positions,
    )
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
        return _run_arm(
            aligner,
            audio_id,
            records,
            target,
            duration_s=duration_s,
            window_s=window_s,
            position_name=position_name,
            position=position,
            text_field=text_field,
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
        "targets": targets,
        "results": results,
        "stability_summary": summarize_stability(results),
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
    """Select evenly distributed, unflagged dialogue cues that fit every arm."""

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
            f"only {len(candidates)} lexical cues fit all stability arms; "
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


def summarize_stability(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate within-cue border movement for each window duration."""

    summaries = []
    for window_s in sorted({float(row["window_size_s"]) for row in results}):
        arms = [row for row in results if float(row["window_size_s"]) == window_s]
        jitters = []
        for cue_id in sorted({str(row["target_cue_id"]) for row in arms}):
            cue_arms = [row for row in arms if str(row["target_cue_id"]) == cue_id]
            starts = [
                float(row["target_result"]["aligned_start_s"]) for row in cue_arms
            ]
            ends = [float(row["target_result"]["aligned_end_s"]) for row in cue_arms]
            jitters.append(
                {
                    "target_cue_id": cue_id,
                    "source_index": cue_arms[0]["target_result"]["source_index"],
                    "start_range_s": max(starts) - min(starts),
                    "end_range_s": max(ends) - min(ends),
                    "max_border_range_s": max(
                        max(starts) - min(starts), max(ends) - min(ends)
                    ),
                }
            )
        border_ranges = [float(row["max_border_range_s"]) for row in jitters]
        summaries.append(
            {
                "window_size_s": window_s,
                "target_count": len(jitters),
                "median_max_border_range_s": median(border_ranges),
                "p90_max_border_range_s": _percentile(border_ranges, 0.9),
                "within_0_16_s_count": sum(
                    value <= 0.16 + 1e-9 for value in border_ranges
                ),
                "broken_order_arm_count": sum(
                    row["target_result"]["status"] != "aligned" for row in arms
                ),
                "edge_arm_count": sum(
                    float(row["target_result"]["score_signals"]["min_edge_distance_s"])
                    <= 0.2
                    for row in arms
                ),
                "targets": jitters,
            }
        )
    return summaries


def build_blind_pairs(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build deterministic randomized labels from middle-position candidates."""

    middle = [row for row in results if row["position_name"] == "middle"]
    pairs = []
    for cue_id in sorted({str(row["target_cue_id"]) for row in middle}):
        arms = sorted(
            (row for row in middle if str(row["target_cue_id"]) == cue_id),
            key=lambda row: float(row["window_size_s"]),
        )
        if len(arms) != 2:
            continue
        if hashlib.sha256(cue_id.encode()).digest()[0] % 2:
            arms.reverse()
        target = arms[0]["target_result"]
        pairs.append(
            {
                "target_cue_id": cue_id,
                "source_index": target["source_index"],
                "text": target["text"],
                "candidates": {
                    label: {
                        "window_size_s": arm["window_size_s"],
                        "start_s": arm["target_result"]["aligned_start_s"],
                        "end_s": arm["target_result"]["aligned_end_s"],
                    }
                    for label, arm in zip(("A", "B"), arms, strict=True)
                },
            }
        )
    return pairs


def _run_arm(
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
) -> dict[str, Any]:
    center_s = _midpoint(target)
    crop_start_s = center_s - position * window_s
    crop_end_s = crop_start_s + window_s
    if crop_start_s < -1e-6 or crop_end_s > duration_s + 1e-6:
        raise ValueError(
            f"target {target['cue_id']} does not fit {window_s}s/{position_name}: "
            f"crop={crop_start_s:.6f}-{crop_end_s:.6f}, audio=0-{duration_s:.6f}"
        )
    crop_start_s = max(0.0, crop_start_s)
    crop_end_s = min(duration_s, crop_end_s)
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


def _percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]
