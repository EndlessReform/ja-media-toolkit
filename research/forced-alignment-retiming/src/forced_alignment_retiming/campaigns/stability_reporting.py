"""Summaries and blind-review pairs for crop-position experiments."""

from __future__ import annotations

import hashlib
from statistics import median
from typing import Any, Iterable


def summarize_stability(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate within-cue border movement for each window duration."""

    summaries = []
    for window_s in sorted({float(row["window_size_s"]) for row in results}):
        requests = [row for row in results if float(row["window_size_s"]) == window_s]
        jitters = []
        for cue_id in sorted({str(row["target_cue_id"]) for row in requests}):
            cue_requests = [
                row for row in requests if str(row["target_cue_id"]) == cue_id
            ]
            starts = [
                float(row["target_result"]["aligned_start_s"]) for row in cue_requests
            ]
            ends = [
                float(row["target_result"]["aligned_end_s"]) for row in cue_requests
            ]
            jitters.append(
                {
                    "target_cue_id": cue_id,
                    "source_index": cue_requests[0]["target_result"]["source_index"],
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
                "broken_order_request_count": sum(
                    row["target_result"]["status"] != "aligned" for row in requests
                ),
                "edge_clamp_request_count": sum(
                    float(row["target_result"]["score_signals"]["min_edge_distance_s"])
                    <= 0.2
                    for row in requests
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
        requests = sorted(
            (row for row in middle if str(row["target_cue_id"]) == cue_id),
            key=lambda row: float(row["window_size_s"]),
        )
        if len(requests) != 2:
            continue
        if hashlib.sha256(cue_id.encode()).digest()[0] % 2:
            requests.reverse()
        target = requests[0]["target_result"]
        pairs.append(
            {
                "target_cue_id": cue_id,
                "source_index": target["source_index"],
                "text": target["text"],
                "candidates": {
                    label: {
                        "window_size_s": request["window_size_s"],
                        "start_s": request["target_result"]["aligned_start_s"],
                        "end_s": request["target_result"]["aligned_end_s"],
                    }
                    for label, request in zip(("A", "B"), requests, strict=True)
                },
            }
        )
    return pairs


def _percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]
