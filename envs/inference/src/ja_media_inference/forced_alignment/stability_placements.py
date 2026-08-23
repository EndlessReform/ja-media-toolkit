"""Place one source cue inside comparable forced-alignment crops."""

from __future__ import annotations

from typing import Any


EDGE_POSITION_NAMES = ("beginning", "middle", "end")


def edge_clearance_crop(
    target: dict[str, Any],
    *,
    duration_s: float,
    window_s: float,
    position_name: str,
    edge_clearance_s: float,
) -> tuple[float, float]:
    """Put the complete source cue near either edge or at crop center."""

    if edge_clearance_s <= 0:
        raise ValueError("edge clearance must be positive")
    source_start_s = float(target["source_start_s"])
    source_end_s = float(target["source_end_s"])
    if position_name == "beginning":
        crop_start_s = source_start_s - edge_clearance_s
    elif position_name == "middle":
        midpoint_s = (source_start_s + source_end_s) / 2
        crop_start_s = midpoint_s - window_s / 2
    elif position_name == "end":
        crop_start_s = source_end_s + edge_clearance_s - window_s
    else:
        raise ValueError(f"unknown crop position: {position_name}")
    crop_end_s = crop_start_s + window_s
    if crop_start_s < -1e-6 or crop_end_s > duration_s + 1e-6:
        raise ValueError(
            f"target {target['cue_id']} does not fit {window_s}s/{position_name}: "
            f"crop={crop_start_s:.6f}-{crop_end_s:.6f}, audio=0-{duration_s:.6f}"
        )
    return max(0.0, crop_start_s), min(duration_s, crop_end_s)


def compare_positions(
    results: list[dict[str, Any]],
    *,
    border_warning_s: float = 0.5,
    edge_clamp_s: float = 0.2,
) -> list[dict[str, Any]]:
    """Compare beginning/end placements with the same cue's middle result."""

    comparisons = []
    keys = sorted(
        {
            (str(row["target_cue_id"]), float(row["window_size_s"]))
            for row in results
        }
    )
    for cue_id, window_s in keys:
        rows = [
            row
            for row in results
            if str(row["target_cue_id"]) == cue_id
            and float(row["window_size_s"]) == window_s
        ]
        by_position = {str(row["position_name"]): row for row in rows}
        middle = by_position.get("middle")
        if middle is None:
            continue
        middle_result = middle["target_result"]
        for position_name in ("beginning", "end"):
            row = by_position.get(position_name)
            if row is None:
                continue
            result = row["target_result"]
            start_shift_s = float(result["aligned_start_s"]) - float(
                middle_result["aligned_start_s"]
            )
            end_shift_s = float(result["aligned_end_s"]) - float(
                middle_result["aligned_end_s"]
            )
            middle_ordered = middle_result["status"] == "aligned"
            edge_ordered = result["status"] == "aligned"
            max_border_shift_s = max(abs(start_shift_s), abs(end_shift_s))
            edge_distance_s = float(
                result["score_signals"]["min_edge_distance_s"]
            )
            outside_crop = _outside_crop(row)
            comparisons.append(
                {
                    "target_cue_id": cue_id,
                    "source_index": result["source_index"],
                    "window_size_s": window_s,
                    "position_name": position_name,
                    "middle_ordered": middle_ordered,
                    "edge_ordered": edge_ordered,
                    "became_broken_at_edge": middle_ordered and not edge_ordered,
                    "start_shift_from_middle_s": start_shift_s,
                    "end_shift_from_middle_s": end_shift_s,
                    "max_border_shift_from_middle_s": max_border_shift_s,
                    "border_shift_over_warning": max_border_shift_s > border_warning_s,
                    "edge_distance_s": edge_distance_s,
                    "clamped_to_audio_edge": edge_distance_s <= edge_clamp_s,
                    "outside_audio_crop": outside_crop,
                    "edge_damaged": middle_ordered
                    and (
                        not edge_ordered
                        or max_border_shift_s > border_warning_s
                        or outside_crop
                    ),
                }
            )
    return comparisons


def _outside_crop(row: dict[str, Any], tolerance_s: float = 0.01) -> bool:
    result = row["target_result"]
    return (
        float(result["aligned_start_s"]) < float(row["crop_start_s"]) - tolerance_s
        or float(result["aligned_end_s"]) > float(row["crop_end_s"]) + tolerance_s
    )
