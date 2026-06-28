from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ja_media_core.transcripts import SubtitleCue

from ja_media_frontend.srt_cleaning.contracts import CleanDecision
from ja_media_frontend.srt_cleaning.result_parser import (
    WindowResult,
    base_window_error,
    to_dlq_row,
)


def group_expected_windows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[source_key(row)].append(row)
    for values in grouped.values():
        values.sort(key=lambda row: int(row["window_number"]))
    return dict(grouped)


def validate_source_windows(
    manifests: list[dict[str, Any]],
    results: dict[str, WindowResult],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for manifest in manifests:
        custom_id = str(manifest["custom_id"])
        result = results.get(custom_id)
        if result is None:
            errors.append(
                base_window_error(
                    custom_id,
                    "missing_result",
                    "No successful batch result was found for this expected window.",
                    manifest,
                )
            )
            continue
        errors.extend(validate_window_decisions(manifest, result))
    return errors


def validate_window_decisions(
    manifest: dict[str, Any],
    result: WindowResult,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    custom_id = str(manifest["custom_id"])
    expected = set(int(index) for index in manifest["active_indexes"])
    seen: set[int] = set()
    for decision in result.decisions:
        if decision.index not in expected:
            errors.append(
                base_window_error(
                    custom_id,
                    "index_mismatch",
                    f"Decision references cue {decision.index}, outside expected active span.",
                    manifest,
                )
            )
            errors[-1]["decision_index"] = decision.index
        if decision.index in seen:
            errors.append(
                base_window_error(
                    custom_id,
                    "duplicate_decision",
                    f"Cue {decision.index} has more than one decision in this window.",
                    manifest,
                )
            )
            errors[-1]["decision_index"] = decision.index
        seen.add(decision.index)
    missing = expected - seen
    if missing:
        errors.append(
            base_window_error(
                custom_id,
                "missing_decision",
                f"Window is missing decisions for cues {sorted(missing)}.",
                manifest,
            )
        )
        errors[-1]["missing_indexes"] = sorted(missing)
    return errors


def collect_source_decisions(
    manifests: list[dict[str, Any]],
    results: dict[str, WindowResult],
    *,
    errors: list[dict[str, Any]],
    dlq: list[dict[str, Any]],
) -> dict[int, CleanDecision]:
    decisions: dict[int, CleanDecision] = {}
    for manifest in manifests:
        result = results.get(str(manifest["custom_id"]))
        if result is None:
            continue
        expected = set(int(index) for index in manifest["active_indexes"])
        for decision in result.decisions:
            if decision.index not in expected:
                continue
            if decision.index in decisions:
                error = base_window_error(
                    str(manifest["custom_id"]),
                    "overlapping_decision",
                    f"Cue {decision.index} already has a decision from another window.",
                    manifest,
                )
                errors.append(error)
                dlq.append(to_dlq_row(error, manifest))
                continue
            decisions[decision.index] = decision
    return decisions


def apply_decisions(
    cues: Iterable[SubtitleCue],
    decisions: dict[int, CleanDecision],
) -> list[SubtitleCue]:
    cleaned: list[SubtitleCue] = []
    for cue in cues:
        decision = decisions.get(cue.index)
        if decision is None or decision.decision in {"asis", "escalate"}:
            cleaned.append(cue)
        elif decision.decision == "edit":
            cleaned.append(
                SubtitleCue(
                    source_path=cue.source_path,
                    index=cue.index,
                    start_s=cue.start_s,
                    end_s=cue.end_s,
                    text=decision.text or "",
                    timing_settings=cue.timing_settings,
                    metadata=dict(cue.metadata),
                )
            )
        elif decision.decision == "remove":
            continue
    return cleaned


def render_decision_rows(
    source_key_value: str,
    manifests: list[dict[str, Any]],
    results: dict[str, WindowResult],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for manifest in manifests:
        custom_id = str(manifest["custom_id"])
        result = results.get(custom_id)
        if result is None:
            continue
        expected = set(int(index) for index in manifest["active_indexes"])
        seen: set[int] = set()
        for position, decision in enumerate(result.decisions, start=1):
            noncompliant_reasons: list[str] = []
            if decision.index not in expected:
                noncompliant_reasons.append("index_mismatch")
            if decision.index in seen:
                noncompliant_reasons.append("duplicate_decision")
            seen.add(decision.index)
            rows.append(
                {
                    "custom_id": custom_id,
                    "source_key": source_key_value,
                    "anilist_id": manifest["anilist_id"],
                    "subtitle_id": manifest["subtitle_id"],
                    "repo_path": manifest["repo_path"],
                    "window_number": manifest["window_number"],
                    "result_position": position,
                    "index": decision.index,
                    "decision": decision.decision,
                    "text": decision.text,
                    "category": decision.category,
                    "within_active_span": decision.index in expected,
                    "compliant": not noncompliant_reasons,
                    "noncompliant_reasons": noncompliant_reasons,
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["source_key"]),
            int(row["window_number"]),
            int(row["result_position"]),
            int(row["index"]),
        )
    )
    return rows


def has_blocking_source_error(
    manifests: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> bool:
    custom_ids = {str(row["custom_id"]) for row in manifests}
    blocking = {
        "missing_result",
        "duplicate_result",
        "index_mismatch",
        "duplicate_decision",
        "missing_decision",
        "overlapping_decision",
    }
    return any(
        error.get("custom_id") in custom_ids and error.get("error_kind") in blocking
        for error in errors
    )


def source_key(row: dict[str, Any]) -> str:
    return f"{row['anilist_id']}:{row['subtitle_id']}:{row['source_sha256']}"


def cleaned_srt_name(row: dict[str, Any]) -> str:
    stem = Path(str(row.get("filename") or row["subtitle_id"])).stem
    return f"{stem}.{str(row['source_sha256'])[:12]}.cleaned.srt"
