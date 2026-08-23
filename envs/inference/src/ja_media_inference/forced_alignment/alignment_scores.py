"""Reduce Qwen token outputs to cue-level review signals."""

from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any, Iterable

from ja_media_inference.forced_alignment.text_units import TokenAlignment


def summarize_token_scores(items: Iterable[TokenAlignment]) -> dict[str, Any]:
    """Return distribution and timing signals without imposing a pass threshold."""

    tokens = list(items)
    if not tokens:
        return {}
    endpoint_rows = [
        alignment.metadata[key]
        for alignment in tokens
        for key in ("start_distribution", "end_distribution")
    ]
    starts = [item.start_s for item in tokens]
    ends = [item.end_s for item in tokens]
    buckets = Counter(starts + ends)
    return {
        "min_endpoint_max_probability": min(
            float(row["max_probability"]) for row in endpoint_rows
        ),
        "mean_endpoint_max_probability": mean(
            float(row["max_probability"]) for row in endpoint_rows
        ),
        "min_top_two_probability_margin": min(
            float(row["top_two_probability_margin"]) for row in endpoint_rows
        ),
        "max_normalized_entropy": max(
            float(row["normalized_entropy"]) for row in endpoint_rows
        ),
        "min_edge_distance_s": min(
            float(row["edge_distance_s"]) for row in endpoint_rows
        ),
        "zero_duration_token_count": sum(item.end_s == item.start_s for item in tokens),
        "reversed_token_count": sum(item.end_s < item.start_s for item in tokens),
        "repeated_timestamp_count": sum(count - 1 for count in buckets.values()),
        "backward_token_count": sum(
            current.start_s < previous.start_s
            for previous, current in zip(tokens, tokens[1:])
        ),
        "aligned_duration_s": max(starts + ends) - min(starts + ends),
        "token_confidence_min": min(
            item.confidence for item in tokens if item.confidence is not None
        ),
    }
