"""Run bounded window-size comparisons against a prepared retiming case."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from typing import Any

from ja_media_core.transcripts import SubtitleCue, format_srt
from ja_media_inference.forced_alignment.alignment_scores import summarize_token_scores
from ja_media_inference.forced_alignment.qwen3_vllm import Qwen3VllmForcedAligner
from ja_media_inference.forced_alignment.text_units import (
    AlignmentTextGroup,
    merge_token_alignments_by_group,
    segment_group_with_nagisa,
)


WINDOW_SIZES_S = (30.0, 60.0, 180.0)
TARGET_FRACTIONS = (0.2, 0.5, 0.8)
FULL_WINDOW_S = 180.0


def compare_case_windows(
    case_manifest: Path,
    *,
    base_url: str,
    output_path: Path | None = None,
    window_sizes_s: tuple[float, ...] = WINDOW_SIZES_S,
    text_field: str = "alignment_text",
) -> Path:
    """Align the same early/middle/late target cues in three window sizes."""

    case_root = case_manifest.parent
    case = json.loads(case_manifest.read_text(encoding="utf-8"))
    audio_path = case_root / case["audio"]["relative_path"]
    records_path = case_root / case["cleaned_subtitle"]["input_cues"]
    records = _read_jsonl(records_path)
    duration_s = float(case["audio"]["duration_s"])
    targets = select_targets(records, duration_s)
    aligner = Qwen3VllmForcedAligner(base_url=base_url)
    windows_dir = case_root / "window-comparison" / "audio"
    windows_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for target_name, target in targets.items():
        center_s = _midpoint(target)
        for window_s in window_sizes_s:
            crop_start_s = max(0.0, min(center_s - window_s / 2, duration_s - window_s))
            crop_end_s = min(duration_s, crop_start_s + window_s)
            members = [
                row for row in records
                if crop_start_s <= _midpoint(row) < crop_end_s
            ]
            crop_path = windows_dir / f"{target_name}-{int(window_s)}s.wav"
            extract_audio_window(audio_path, crop_path, crop_start_s, crop_end_s)
            results.append(
                align_window(
                    aligner,
                    crop_path,
                    members,
                    target_id=str(target["cue_id"]),
                    target_name=target_name,
                    crop_start_s=crop_start_s,
                    crop_end_s=crop_end_s,
                    text_field=text_field,
                )
            )

    report = {
        "schema_name": "ja-media.forced-alignment.window-comparison",
        "schema_version": "1.0.0",
        "case": case["case"],
        "backend": {"type": "qwen3-vllm", "model": aligner.model},
        "window_sizes_s": list(window_sizes_s),
        "target_fractions": list(TARGET_FRACTIONS),
        "text_field": text_field,
        "results": results,
    }
    destination = output_path or case_root / "window-comparison" / "results.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return destination


def align_full_case(
    case_manifest: Path,
    *,
    base_url: str,
    window_s: float = FULL_WINDOW_S,
    text_field: str = "alignment_text",
) -> Path:
    """Align every prepared cue once and write an episode-clock candidate SRT."""

    case_root = case_manifest.parent
    case = json.loads(case_manifest.read_text(encoding="utf-8"))
    audio_path = case_root / case["audio"]["relative_path"]
    records = _read_jsonl(case_root / case["cleaned_subtitle"]["input_cues"])
    duration_s = float(case["audio"]["duration_s"])
    aligner = Qwen3VllmForcedAligner(base_url=base_url)
    run_root = case_root / "full-alignment"
    audio_dir = run_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    window_results = []
    for window_index, crop_start_s in enumerate(
        _window_starts(duration_s, window_s), start=1
    ):
        crop_end_s = min(duration_s, crop_start_s + window_s)
        members = [
            row for row in records
            if crop_start_s <= _midpoint(row) < crop_end_s
        ]
        if not members:
            continue
        crop_path = audio_dir / f"window-{window_index:04d}.wav"
        extract_audio_window(audio_path, crop_path, crop_start_s, crop_end_s)
        window_results.append(
            align_window(
                aligner,
                crop_path,
                members,
                target_id=None,
                target_name=f"window-{window_index:04d}",
                crop_start_s=crop_start_s,
                crop_end_s=crop_end_s,
                text_field=text_field,
            )
        )
    aligned_by_id = {
        cue["cue_id"]: cue
        for window in window_results
        for cue in window["cues"]
    }
    if set(aligned_by_id) != {row["cue_id"] for row in records}:
        raise RuntimeError("full alignment did not return every prepared cue exactly once")
    output_srt = run_root / "retimed.srt"
    output_srt.write_text(format_srt(_retimed_cues(records, aligned_by_id)))
    report_path = run_root / "results.json"
    report = {
        "schema_name": "ja-media.forced-alignment.full-result",
        "schema_version": "1.0.0",
        "case": case["case"],
        "backend": {"type": "qwen3-vllm", "model": aligner.model},
        "window_duration_s": window_s,
        "text_field": text_field,
        "retimed_srt": output_srt.relative_to(case_root).as_posix(),
        "windows": window_results,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report_path


def select_targets(
    records: list[dict[str, Any]], duration_s: float
) -> dict[str, dict[str, Any]]:
    """Choose fixed source cues nearest 20%, 50%, and 80% of the episode."""

    if not records:
        raise ValueError("the prepared case has no alignment cues")
    names = ("early", "middle", "late")
    return {
        name: min(records, key=lambda row: abs(_midpoint(row) - duration_s * fraction))
        for name, fraction in zip(names, TARGET_FRACTIONS, strict=True)
    }


def align_window(
    aligner: Qwen3VllmForcedAligner,
    audio_path: Path,
    records: list[dict[str, Any]],
    *,
    target_id: str | None,
    target_name: str,
    crop_start_s: float,
    crop_end_s: float,
    text_field: str = "alignment_text",
) -> dict[str, Any]:
    """Align one unpadded crop and convert local times to the episode clock."""

    groups = [
        AlignmentTextGroup(
            id=str(row["cue_id"]),
            text=str(row[text_field]),
            metadata={
                "source_index": row["source_index"],
                "cleaned_index": row["cleaned_index"],
            },
        )
        for row in records
    ]
    tokens = [token for group in groups for token in segment_group_with_nagisa(group)]
    started = time.monotonic()
    token_alignments = aligner.align_tokens(audio_path=audio_path, tokens=tokens)
    elapsed_s = time.monotonic() - started
    merged = merge_token_alignments_by_group(groups, token_alignments)
    tokens_by_group = {
        group.id: [item for item in token_alignments if item.token.group_id == group.id]
        for group in groups
    }
    cue_results = []
    for row in records:
        local = merged[str(row["cue_id"])]
        cue_results.append(
            {
                "cue_id": row["cue_id"],
                "source_index": row["source_index"],
                "cleaned_index": row["cleaned_index"],
                "text": row[text_field],
                "source_start_s": row["source_start_s"],
                "source_end_s": row["source_end_s"],
                "aligned_start_s": _global_time(local.start_s, crop_start_s),
                "aligned_end_s": _global_time(local.end_s, crop_start_s),
                "status": local.status,
                "token_count": local.metadata.get("token_count", 0),
                "score_signals": summarize_token_scores(
                    tokens_by_group[str(row["cue_id"])]
                ),
            }
        )
    target = (
        next(item for item in cue_results if item["cue_id"] == target_id)
        if target_id is not None
        else None
    )
    return {
        "target": target_name,
        "target_cue_id": target_id,
        "crop_start_s": crop_start_s,
        "crop_end_s": crop_end_s,
        "window_duration_s": crop_end_s - crop_start_s,
        "cue_count": len(groups),
        "token_count": len(tokens),
        "request_elapsed_s": elapsed_s,
        "target_result": target,
        "cues": cue_results,
    }


def extract_audio_window(
    source: Path, destination: Path, start_s: float, end_s: float
) -> None:
    """Decode an exact, unpadded source-clock crop to mono 16 kHz PCM."""

    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-ss", f"{start_s:.3f}",
            "-i", str(source), "-t", f"{end_s - start_s:.3f}",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destination),
        ],
        check=True,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _midpoint(row: dict[str, Any]) -> float:
    return (float(row["source_start_s"]) + float(row["source_end_s"])) / 2


def _global_time(value: float | None, crop_start_s: float) -> float | None:
    return None if value is None else value + crop_start_s


def _window_starts(duration_s: float, window_s: float) -> list[float]:
    if window_s <= 0 or window_s > 180:
        raise ValueError("full alignment window must be greater than 0 and at most 180s")
    return [float(start) for start in range(0, int(duration_s) + 1, int(window_s))]


def _retimed_cues(records, aligned_by_id) -> list[SubtitleCue]:
    cues = []
    for record in records:
        aligned = aligned_by_id[record["cue_id"]]
        start_s = aligned["aligned_start_s"]
        end_s = aligned["aligned_end_s"]
        if start_s is None or end_s is None:
            raise RuntimeError(f"cue has no aligned timing: {record['cue_id']}")
        cues.append(
            SubtitleCue(
                source_path=None,
                index=int(record["cleaned_index"]),
                start_s=float(start_s),
                end_s=float(end_s),
                text=str(aligned["text"]),
                metadata={"cue_id": record["cue_id"]},
            )
        )
    return cues
