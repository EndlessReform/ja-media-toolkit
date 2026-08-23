"""Checks for client-side timestamp distribution metrics."""

import pytest

from ja_media_inference.forced_alignment.audio_probe import audio_edge_distance
from ja_media_inference.forced_alignment.qwen3_vllm import _distribution_metrics


def test_distribution_metrics_separate_sharp_and_flat_rows() -> None:
    sharp = _distribution_metrics([0.0, 0.99, 0.01])
    flat = _distribution_metrics([1 / 3, 1 / 3, 1 / 3])

    assert sharp["argmax_index"] == 1
    assert sharp["distribution_kind"] == "probabilities"
    assert sharp["max_probability"] == 0.99
    assert sharp["top_two_probability_margin"] == 0.98
    assert sharp["normalized_entropy"] < 0.06
    assert flat["max_probability"] == pytest.approx(1 / 3)
    assert flat["top_two_probability_margin"] == 0.0
    assert flat["normalized_entropy"] == pytest.approx(1.0)


def test_audio_edge_distance_uses_crop_duration_and_clamps_outliers() -> None:
    assert audio_edge_distance(78.64, 84.0) == pytest.approx(5.36)
    assert audio_edge_distance(90.0, 84.0) == 0.0
