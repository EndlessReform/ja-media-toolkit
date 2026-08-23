"""Cue-level score reduction checks."""

from ja_media_inference.forced_alignment.alignment_scores import summarize_token_scores
from ja_media_inference.forced_alignment.text_units import AlignmentToken, TokenAlignment


def test_summarizes_distribution_and_structural_signals() -> None:
    token = AlignmentToken("t1", "話", "g1", 0)
    item = TokenAlignment(
        token,
        1.0,
        1.0,
        confidence=0.8,
        metadata={
            "start_distribution": _row(0.8, 2.0, 0.2, 1.0),
            "end_distribution": _row(0.9, 3.0, 0.1, 1.5),
        },
    )

    result = summarize_token_scores([item])

    assert result["min_endpoint_max_probability"] == 0.8
    assert result["min_top_two_probability_margin"] == 2.0
    assert result["max_normalized_entropy"] == 0.2
    assert result["zero_duration_token_count"] == 1
    assert result["reversed_token_count"] == 0
    assert result["repeated_timestamp_count"] == 1


def test_counts_reversed_token_separately_from_cross_token_order() -> None:
    token = AlignmentToken("t1", "逆", "g1", 0)
    item = TokenAlignment(
        token,
        2.0,
        1.0,
        confidence=0.4,
        metadata={
            "start_distribution": _row(0.4, 0.1, 0.6, 1.0),
            "end_distribution": _row(0.5, 0.2, 0.5, 1.0),
        },
    )

    result = summarize_token_scores([item])

    assert result["reversed_token_count"] == 1
    assert result["backward_token_count"] == 0


def _row(probability, margin, entropy, edge):
    return {
        "max_probability": probability,
        "top_two_probability_margin": margin,
        "normalized_entropy": entropy,
        "edge_distance_s": edge,
    }
