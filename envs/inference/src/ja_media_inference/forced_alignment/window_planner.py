"""Turn an audio-derived VAD plan into core and boundary alignment requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AlignmentWindow:
    """One unpadded VAD core or a short probe across a VAD boundary."""

    index: int
    kind: str
    core_start_s: float
    core_end_s: float
    crop_start_s: float
    crop_end_s: float
    boundary_s: float | None = None


def plan_alignment_windows(
    vad_payload: dict[str, Any],
    *,
    duration_s: float,
    boundary_radius_s: float,
) -> list[AlignmentWindow]:
    """Keep VAD cores unpadded and add short probes across their boundaries."""

    if duration_s <= 0:
        raise ValueError("episode duration must be positive")
    if boundary_radius_s <= 0:
        raise ValueError("boundary radius must be positive")
    chunks = vad_payload.get("split_chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("VAD plan must contain non-empty split_chunks")

    cores: list[tuple[float, float]] = []
    previous_end_s = 0.0
    for chunk in chunks:
        start_s = float(chunk["start_s"])
        end_s = float(chunk["end_s"])
        if abs(start_s - previous_end_s) > 0.001:
            raise ValueError("VAD core windows must be contiguous from episode start")
        if end_s <= start_s:
            raise ValueError("VAD core windows must have positive duration")
        cores.append((start_s, end_s))
        previous_end_s = end_s
    if abs(previous_end_s - duration_s) > 0.001:
        raise ValueError("VAD core windows must cover the complete episode")
    windows: list[AlignmentWindow] = []
    for core_index, (start_s, end_s) in enumerate(cores):
        windows.append(
            AlignmentWindow(
                index=len(windows) + 1,
                kind="core",
                core_start_s=start_s,
                core_end_s=end_s,
                crop_start_s=start_s,
                crop_end_s=end_s,
            )
        )
        if core_index == len(cores) - 1:
            continue
        windows.append(
            AlignmentWindow(
                index=len(windows) + 1,
                kind="boundary",
                core_start_s=start_s,
                core_end_s=end_s,
                crop_start_s=max(0.0, end_s - boundary_radius_s),
                crop_end_s=min(duration_s, end_s + boundary_radius_s),
                boundary_s=end_s,
            )
        )
    return windows


def records_for_window(
    records: list[dict[str, Any]],
    window: AlignmentWindow,
    *,
    duration_s: float | None = None,
) -> list[dict[str, Any]]:
    """Assign core text by midpoint and keep out-of-range text at audio edges."""

    if window.kind == "core":
        return [
            row
            for row in records
            if (
                window.core_start_s <= _midpoint(row) < window.core_end_s
                or (
                    duration_s is not None
                    and window.core_start_s == 0.0
                    and _midpoint(row) < 0.0
                )
                or (
                    duration_s is not None
                    and abs(window.core_end_s - duration_s) <= 0.001
                    and _midpoint(row) >= duration_s
                )
            )
        ]
    return [
        row
        for row in records
        if float(row["source_end_s"]) > window.crop_start_s
        and float(row["source_start_s"]) < window.crop_end_s
    ]


def select_alignment_candidates(
    records: list[dict[str, Any]], window_results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Choose one result per cue from overlapping VAD-window requests."""

    candidates: dict[str, list[dict[str, Any]]] = {}
    for window in window_results:
        for cue in window["cues"]:
            tagged = {
                **cue,
                "alignment_window_index": window["window_index"],
                "alignment_window_kind": window["window_kind"],
                "alignment_core_start_s": window["core_start_s"],
                "alignment_core_end_s": window["core_end_s"],
            }
            candidates.setdefault(str(cue["cue_id"]), []).append(tagged)

    selected: list[dict[str, Any]] = []
    for record in records:
        cue_id = str(record["cue_id"])
        choices = candidates.get(cue_id, [])
        if not choices:
            raise RuntimeError(f"no alignment candidate returned for {cue_id}")
        winner = max(choices, key=_candidate_rank)
        selected.append({**winner, "alignment_candidate_count": len(choices)})
    return selected


def _candidate_rank(cue: dict[str, Any]) -> tuple[float, ...]:
    signals = cue.get("score_signals") or {}
    anomaly_count = sum(
        int(signals.get(name) or 0)
        for name in (
            "zero_duration_token_count",
            "reversed_token_count",
            "backward_token_count",
        )
    )
    plausible_envelope = (
        float(signals.get("min_edge_distance_s") or 0.0) > 0.2
        and float(signals.get("aligned_duration_s") or 0.0) <= 15.0
    )
    ordered_tokens = anomaly_count == 0
    midpoint_s = (float(cue["source_start_s"]) + float(cue["source_end_s"])) / 2
    owns_source_midpoint = cue["alignment_window_kind"] == "core" and (
        float(cue["alignment_core_start_s"])
        <= midpoint_s
        < float(cue["alignment_core_end_s"])
    )
    return (
        float(plausible_envelope),
        float(owns_source_midpoint),
        float(ordered_tokens),
        float(cue.get("status") == "aligned"),
        -float(anomaly_count),
        float(signals.get("min_edge_distance_s") or 0.0),
        -float(signals.get("aligned_duration_s") or 0.0),
        -float(signals.get("max_normalized_entropy") or 1.0),
        float(signals.get("min_endpoint_max_probability") or 0.0),
        -float(cue["alignment_window_index"]),
    )


def _midpoint(row: dict[str, Any]) -> float:
    return (float(row["source_start_s"]) + float(row["source_end_s"])) / 2
