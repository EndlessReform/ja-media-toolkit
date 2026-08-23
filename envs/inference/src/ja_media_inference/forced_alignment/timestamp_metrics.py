"""Vectorized review metrics for timestamp-class probability rows."""

from __future__ import annotations

from collections.abc import Sequence
import math
from typing import Any

import numpy as np


def summarize_distribution_rows(
    values: Sequence[Sequence[float]] | np.ndarray,
) -> list[dict[str, Any]]:
    """Reduce timestamp rows without constructing millions of Python floats."""

    matrix = np.asarray(values, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] == 0:
        raise ValueError("timestamp distributions must be a non-empty 2D matrix")

    row_sums = matrix.sum(axis=1)
    probability_rows = np.logical_and(
        np.all(matrix >= 0, axis=1),
        np.isclose(row_sums, 1.0, rtol=1e-3, atol=1e-3),
    )
    probabilities = np.empty_like(matrix)
    if np.any(probability_rows):
        probabilities[probability_rows] = (
            matrix[probability_rows] / row_sums[probability_rows, None]
        )
    if np.any(~probability_rows):
        logits = matrix[~probability_rows]
        weights = np.exp(logits - logits.max(axis=1, keepdims=True))
        probabilities[~probability_rows] = weights / weights.sum(
            axis=1, keepdims=True
        )

    best_indices = probabilities.argmax(axis=1)
    best = probabilities[np.arange(len(probabilities)), best_indices]
    if probabilities.shape[1] > 1:
        top_two = np.partition(probabilities, -2, axis=1)[:, -2:]
        margins = np.abs(top_two[:, 1] - top_two[:, 0])
    else:
        margins = best
    log_probabilities = np.zeros_like(probabilities)
    np.log(
        probabilities,
        out=log_probabilities,
        where=probabilities > 0,
    )
    entropy = -np.sum(probabilities * log_probabilities, axis=1)
    normalized_entropy = (
        entropy / math.log(probabilities.shape[1])
        if probabilities.shape[1] > 1
        else np.zeros(len(probabilities), dtype=np.float32)
    )

    return [
        {
            "argmax_index": int(best_indices[index]),
            "distribution_kind": (
                "probabilities" if probability_rows[index] else "logits"
            ),
            "max_probability": float(best[index]),
            "top_two_probability_margin": float(margins[index]),
            "normalized_entropy": float(normalized_entropy[index]),
        }
        for index in range(len(probabilities))
    ]
