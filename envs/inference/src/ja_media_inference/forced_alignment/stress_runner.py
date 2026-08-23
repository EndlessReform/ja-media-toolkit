"""Bounded client-concurrency sweep for the compact alignment adapter."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from statistics import median
import time
from typing import Any, Callable

from ja_media_inference.forced_alignment.case_runner import _midpoint, _read_jsonl
from ja_media_inference.forced_alignment.qwen3_adapter_client import Qwen3AdapterClient
from ja_media_inference.forced_alignment.stability_runner import (
    select_lexical_targets,
)
from ja_media_inference.forced_alignment.text_units import (
    AlignmentTextGroup,
    segment_group_with_nagisa,
)


def run_concurrency_sweep(
    case_manifest: Path,
    *,
    base_url: str,
    levels: tuple[int, ...] = (1, 2, 4, 8, 16, 32),
    window_s: float = 60.0,
    text_field: str = "alignment_text",
) -> Path:
    """Run one identical compact alignment wave at each client concurrency."""

    if not levels or any(level <= 0 for level in levels):
        raise ValueError("concurrency levels must be positive")
    case_root = case_manifest.parent
    case = json.loads(case_manifest.read_text(encoding="utf-8"))
    duration_s = float(case["audio"]["duration_s"])
    records = _read_jsonl(case_root / case["cleaned_subtitle"]["input_cues"])
    target = select_lexical_targets(
        records,
        duration_s=duration_s,
        sample_count=1,
        max_window_s=window_s,
        positions=(("middle", 0.5),),
    )[0]
    crop_start_s = _midpoint(target) - window_s / 2
    crop_end_s = crop_start_s + window_s
    members = [row for row in records if crop_start_s <= _midpoint(row) < crop_end_s]
    groups = [
        AlignmentTextGroup(id=str(row["cue_id"]), text=str(row[text_field]))
        for row in members
    ]
    tokens = [token for group in groups for token in segment_group_with_nagisa(group)]
    client = Qwen3AdapterClient(base_url=base_url)
    audio_id = client.cache_audio(case["audio"])

    def request() -> dict[str, float | int]:
        result = client.align_crop_profiled(
            audio_id=audio_id,
            crop_start_s=crop_start_s,
            crop_end_s=crop_end_s,
            tokens=tokens,
        )
        return result.profile

    request()
    measurements = []
    for level in levels:
        measurement = _run_wave(request, concurrency=level, audio_seconds=window_s)
        measurements.append(measurement)
        if measurement["failed_requests"]:
            break
    report = {
        "schema_name": "ja-media.forced-alignment.concurrency-sweep",
        "schema_version": "3.0.0",
        "case": case["case"],
        "backend": client.name,
        "window_s": window_s,
        "cue_count": len(groups),
        "token_count": len(tokens),
        "levels": measurements,
        "suggested_concurrency": suggest_concurrency(measurements),
    }
    output_root = case_root / "stress"
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / "concurrency-sweep.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return destination


def suggest_concurrency(measurements: list[dict[str, Any]]) -> int:
    """Choose the smallest level within 90% of peak successful throughput."""

    successful = [row for row in measurements if not row["failed_requests"]]
    if not successful:
        raise RuntimeError("all concurrency sweep levels failed")
    best = max(float(row["requests_per_s"]) for row in successful)
    return int(
        next(
            row["concurrency"]
            for row in successful
            if float(row["requests_per_s"]) >= best * 0.9
        )
    )


def _run_wave(
    request: Callable[[], dict[str, float | int]],
    *,
    concurrency: int,
    audio_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    latencies: list[float] = []
    errors: list[str] = []
    profiles: list[dict[str, float | int]] = []

    def timed_request() -> tuple[float, dict[str, float | int]]:
        request_started = time.monotonic()
        profile = request()
        roundtrip_s = time.monotonic() - request_started
        profile = {**profile, "client_roundtrip_s": roundtrip_s}
        if "adapter_before_response_s" in profile:
            profile["adapter_response_and_lan_s"] = max(
                0.0, roundtrip_s - float(profile["adapter_before_response_s"])
            )
        return roundtrip_s, profile

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(timed_request) for _ in range(concurrency)]
        for future in as_completed(futures):
            try:
                latency, profile = future.result()
                latencies.append(latency)
                profiles.append(profile)
            except Exception as exc:  # retain a bounded server/client diagnostic
                errors.append(f"{type(exc).__name__}: {exc}")
    wall_s = time.monotonic() - started
    completed = len(latencies)
    return {
        "concurrency": concurrency,
        "completed_requests": completed,
        "failed_requests": len(errors),
        "wall_s": wall_s,
        "requests_per_s": completed / wall_s,
        "audio_realtime_factor": completed * audio_seconds / wall_s,
        "median_latency_s": median(latencies) if latencies else None,
        "max_latency_s": max(latencies) if latencies else None,
        "median_profile": _aggregate_profile(profiles, median),
        "max_profile": _aggregate_profile(profiles, max),
        "errors": errors[:3],
    }


def _aggregate_profile(
    profiles: list[dict[str, float | int]],
    aggregate: Callable[[list[float]], float],
) -> dict[str, float]:
    keys = sorted({key for profile in profiles for key in profile})
    return {
        key: aggregate([float(profile[key]) for profile in profiles if key in profile])
        for key in keys
    }
