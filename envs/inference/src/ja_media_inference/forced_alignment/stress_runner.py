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
    audio_pattern: str = "unique",
) -> Path:
    """Measure concurrency with unique episode crops or a warm repeated crop."""

    if not levels or any(level <= 0 for level in levels):
        raise ValueError("concurrency levels must be positive")
    if audio_pattern not in {"unique", "repeated"}:
        raise ValueError("audio_pattern must be 'unique' or 'repeated'")
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
    members = [
        row
        for row in records
        if crop_start_s <= _midpoint(row) < crop_start_s + window_s
    ]
    groups = [
        AlignmentTextGroup(id=str(row["cue_id"]), text=str(row[text_field]))
        for row in members
    ]
    tokens = [token for group in groups for token in segment_group_with_nagisa(group)]
    client = Qwen3AdapterClient(base_url=base_url)
    audio_id = client.cache_audio(case["audio"])

    def request(start_s: float) -> dict[str, float | int]:
        result = client.align_crop_profiled(
            audio_id=audio_id,
            crop_start_s=start_s,
            crop_end_s=start_s + window_s,
            tokens=tokens,
        )
        return result.profile

    request_count = 1 + sum(levels)
    if audio_pattern == "unique":
        crop_starts = _evenly_spaced_crop_starts(
            duration_s=duration_s,
            window_s=window_s,
            count=request_count,
        )
    else:
        crop_starts = [crop_start_s] * request_count
    request(crop_starts[0])
    measurements = []
    next_crop = 1
    for level in levels:
        wave_starts = crop_starts[next_crop : next_crop + level]
        next_crop += level
        measurement = _run_wave(
            [lambda start_s=start_s: request(start_s) for start_s in wave_starts],
            audio_seconds=window_s,
        )
        measurements.append(measurement)
        if measurement["failed_requests"]:
            break
    report = {
        "schema_name": "ja-media.forced-alignment.concurrency-sweep",
        "schema_version": "4.0.0",
        "case": case["case"],
        "backend": client.name,
        "window_s": window_s,
        "cue_count": len(groups),
        "token_count": len(tokens),
        "audio_pattern": audio_pattern,
        "distinct_crop_count": len(set(crop_starts)),
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
    requests: list[Callable[[], dict[str, float | int]]],
    *,
    audio_seconds: float,
) -> dict[str, Any]:
    concurrency = len(requests)
    started = time.monotonic()
    latencies: list[float] = []
    errors: list[str] = []
    profiles: list[dict[str, float | int]] = []

    def timed_request(
        request: Callable[[], dict[str, float | int]],
    ) -> tuple[float, dict[str, float | int]]:
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
        futures = [executor.submit(timed_request, request) for request in requests]
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


def _evenly_spaced_crop_starts(
    *, duration_s: float, window_s: float, count: int
) -> list[float]:
    """Return distinct full-length crops spread across an episode."""

    if count <= 0:
        raise ValueError("crop count must be positive")
    available_s = duration_s - window_s
    if available_s <= 0:
        raise ValueError("audio duration must be longer than the stress window")
    step_s = available_s / count
    return [(index + 0.5) * step_s for index in range(count)]


def _aggregate_profile(
    profiles: list[dict[str, float | int]],
    aggregate: Callable[[list[float]], float],
) -> dict[str, float]:
    keys = sorted({key for profile in profiles for key in profile})
    return {
        key: aggregate([float(profile[key]) for profile in profiles if key in profile])
        for key in keys
    }
