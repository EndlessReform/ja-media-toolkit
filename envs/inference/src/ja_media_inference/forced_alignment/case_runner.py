"""Run bounded window-size comparisons against a prepared retiming case."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
from typing import Any, TypeVar

from ja_media_core.transcripts import SubtitleCue, format_srt
from ja_media_inference.forced_alignment.qwen3_adapter_client import Qwen3AdapterClient
from ja_media_inference.forced_alignment.window_execution import (
    align_prepared_remote_window,
    align_remote_window,
    prepare_alignment_window,
)
from ja_media_inference.forced_alignment.window_planner import (
    plan_alignment_windows,
    records_for_window,
    select_alignment_candidates,
)


WINDOW_SIZES_S = (30.0, 60.0, 180.0)
TARGET_FRACTIONS = (0.2, 0.5, 0.8)
_WindowJob = TypeVar("_WindowJob")
_WindowResult = TypeVar("_WindowResult")


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
    records_path = case_root / case["cleaned_subtitle"]["input_cues"]
    records = _read_jsonl(records_path)
    duration_s = float(case["audio"]["duration_s"])
    targets = select_targets(records, duration_s)
    aligner = Qwen3AdapterClient(base_url=base_url)
    audio_id = aligner.cache_audio(case["audio"])
    results = []
    for target_name, target in targets.items():
        center_s = _midpoint(target)
        for window_s in window_sizes_s:
            crop_start_s = max(0.0, min(center_s - window_s / 2, duration_s - window_s))
            crop_end_s = min(duration_s, crop_start_s + window_s)
            members = [
                row for row in records if crop_start_s <= _midpoint(row) < crop_end_s
            ]
            results.append(
                align_remote_window(
                    aligner,
                    audio_id,
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
        "backend": {"type": aligner.name, "model": aligner.model},
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
    vad_plan_path: Path,
    boundary_radius_s: float = 30.0,
    text_field: str = "alignment_text",
    concurrency: int = 32,
) -> Path:
    """Align VAD-windowed audio and reconcile duplicate boundary cues."""

    if concurrency <= 0:
        raise ValueError("alignment concurrency must be positive")
    case_root = case_manifest.parent
    case = json.loads(case_manifest.read_text(encoding="utf-8"))
    records = _read_jsonl(case_root / case["cleaned_subtitle"]["input_cues"])
    duration_s = float(case["audio"]["duration_s"])
    vad_payload = json.loads(vad_plan_path.read_text(encoding="utf-8"))
    aligner = Qwen3AdapterClient(base_url=base_url)
    audio_id = aligner.cache_audio(case["audio"])
    run_root = case_root / "full-alignment"
    run_root.mkdir(parents=True, exist_ok=True)
    planned_windows = plan_alignment_windows(
        vad_payload, duration_s=duration_s, boundary_radius_s=boundary_radius_s
    )
    jobs = []
    for window in planned_windows:
        members = records_for_window(records, window, duration_s=duration_s)
        if not members:
            continue
        jobs.append((window, prepare_alignment_window(members, text_field)))

    def align_window(job):  # type: ignore[no-untyped-def]
        window, prepared = job
        result = align_prepared_remote_window(
            aligner,
            audio_id,
            prepared,
            target_id=None,
            target_name=f"window-{window.index:04d}",
            crop_start_s=window.crop_start_s,
            crop_end_s=window.crop_end_s,
        )
        result.update(
            window_index=window.index,
            window_kind=window.kind,
            core_start_s=window.core_start_s,
            core_end_s=window.core_end_s,
            boundary_s=window.boundary_s,
        )
        return result

    window_results = _map_concurrently_in_order(
        jobs,
        concurrency=concurrency,
        operation=align_window,
    )
    selected_cues = select_alignment_candidates(records, window_results)
    aligned_by_id = {cue["cue_id"]: cue for cue in selected_cues}
    output_srt = run_root / "retimed.srt"
    output_srt.write_text(format_srt(_retimed_cues(records, aligned_by_id)))
    report_path = run_root / "results.json"
    report = {
        "schema_name": "ja-media.forced-alignment.full-result",
        "schema_version": "1.1.0",
        "case": case["case"],
        "backend": {"type": aligner.name, "model": aligner.model},
        "window_policy": "vad-core-plus-boundary-probe-v1",
        "boundary_radius_s": boundary_radius_s,
        "concurrency": concurrency,
        "vad_plan": str(vad_plan_path),
        "text_field": text_field,
        "retimed_srt": output_srt.relative_to(case_root).as_posix(),
        "selected_cues": selected_cues,
        "windows": window_results,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report_path


def _map_concurrently_in_order(
    jobs: list[_WindowJob],
    *,
    concurrency: int,
    operation: Callable[[_WindowJob], _WindowResult],
) -> list[_WindowResult]:
    """Run independent window calls concurrently while preserving planner order."""

    if concurrency <= 0:
        raise ValueError("alignment concurrency must be positive")
    if concurrency == 1:
        return [operation(job) for job in jobs]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        return list(executor.map(operation, jobs))


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


def extract_audio_window(
    source: Path, destination: Path, start_s: float, end_s: float
) -> None:
    """Decode an exact, unpadded source-clock crop to mono 16 kHz PCM."""

    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start_s:.3f}",
            "-i",
            str(source),
            "-t",
            f"{end_s - start_s:.3f}",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        check=True,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _midpoint(row: dict[str, Any]) -> float:
    return (float(row["source_start_s"]) + float(row["source_end_s"])) / 2


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
