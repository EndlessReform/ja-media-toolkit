"""Gate and rank one episode's Kitsunekko candidates against an embedded anchor."""

from __future__ import annotations

import json
from math import sqrt
from pathlib import Path
import shutil

from ja_media_core.subtitle_lid import SubtitleLanguage, analyze_subtitle_language
from ja_media_core.subsync import subtitle_anchor_fit_score, subtitle_goodness_of_fit
from ja_media_core.transcripts import SubtitleCue

from forced_alignment_retiming.cases import CanonicalCase
from forced_alignment_retiming.subtitles import positive_cues, read_cues


PREFERRED_LANGUAGES = {SubtitleLanguage.JAPANESE, SubtitleLanguage.BILINGUAL}


def pair_case(
    case: CanonicalCase,
    canonical: dict[str, object],
    audio: dict[str, object],
    anchors: list[dict[str, object]],
    candidate_manifest: Path,
) -> Path:
    """Write the final pre-cleaning source pairing for one canonical episode."""

    case_root = candidate_manifest.parent
    anchor_rows, selected_anchor = _rank_anchors(
        anchors, case_root, float(audio["duration_s"])
    )
    candidates = json.loads(candidate_manifest.read_text())["candidates"]
    ranked = _rank_candidates(
        case, candidates, case_root, selected_anchor["cues"] if selected_anchor else None
    )
    selected = _select_candidate(ranked)
    source_path = _copy_selected_source(selected, case_root) if selected else None
    result = {
        "schema_version": 1,
        "case": {
            "name": case.name,
            "namespace": case.namespace,
            "series_id": case.series_id,
            "episode": case.episode,
        },
        "canonical": canonical,
        "audio": audio,
        "anchors": anchor_rows,
        "selected_anchor_id": (
            selected_anchor["subtitle_input_id"] if selected_anchor else None
        ),
        "candidates": ranked,
        "selected_subtitle_id": selected["subtitle_id"] if selected else None,
        "source_path": source_path,
        "status": "paired" if selected else "manual_selection_required",
    }
    destination = case_root / "candidate-ranking.json"
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return destination


def _rank_anchors(
    anchors: list[dict[str, object]], case_root: Path, duration_s: float
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    rows = []
    usable = []
    for item in anchors:
        row = dict(item)
        try:
            cues = read_cues(case_root / str(item["relative_path"]), str(item["codec"]))
            spans = positive_cues(cues)
            starts = [cue.start_s for cue in spans]
            ends = [cue.end_s for cue in spans]
            row.update(
                cue_count=len(spans),
                first_cue_s=min(starts, default=None),
                last_cue_s=max(ends, default=None),
            )
            reasons = []
            if len(spans) < 100:
                reasons.append("fewer_than_100_positive_cues")
            if starts and min(starts) > duration_s * 0.2:
                reasons.append("starts_after_first_20_percent")
            if not ends or max(ends) < duration_s * 0.8:
                reasons.append("ends_before_last_20_percent")
            row["gate_reasons"] = reasons
            row["status"] = "eligible" if not reasons else "rejected"
            if not reasons:
                usable.append({**row, "cues": cues})
        except (OSError, UnicodeError, ValueError) as error:
            row.update(status="rejected", gate_reasons=[f"parse_error: {error}"])
        rows.append(row)
    usable.sort(key=lambda item: (-int(item["cue_count"]), str(item["subtitle_input_id"])))
    return rows, usable[0] if usable else None


def _rank_candidates(
    case: CanonicalCase,
    candidates: list[dict[str, object]],
    case_root: Path,
    anchor_cues: tuple[SubtitleCue, ...] | None,
) -> list[dict[str, object]]:
    rows = [
        _candidate_row(case, item, case_root, anchor_cues)
        for item in candidates
    ]
    preferred_exists = any(
        row.get("status") == "eligible"
        and row.get("language") in {"japanese", "bilingual"}
        for row in rows
    )
    for row in rows:
        row["considered"] = row.get("status") == "eligible" and not (
            preferred_exists and row.get("language") == "unknown"
        )
    rows.sort(
        key=lambda row: (
            not bool(row["considered"]),
            -float(row.get("selection_score") or 0.0),
            str(row.get("subtitle_id") or ""),
        )
    )
    return rows


def _candidate_row(
    case: CanonicalCase,
    item: dict[str, object],
    case_root: Path,
    anchor_cues: tuple[SubtitleCue, ...] | None,
) -> dict[str, object]:
    row = dict(item)
    metadata = item.get("metadata")
    if item.get("status") != "ok":
        return _rejected(row, "download_failed")
    if not isinstance(metadata, dict):
        return _rejected(row, "missing_metadata")
    if int(item.get("mapped_episode") or 0) != int(case.episode):
        return _rejected(row, "episode_mapping_mismatch")
    metadata_episode = metadata.get("episode_local")
    if metadata_episode is not None and int(metadata_episode) != int(case.episode):
        return _rejected(row, "episode_metadata_disagrees_with_mapping")
    warnings = [] if metadata_episode is not None else ["episode_metadata_missing"]
    extension = str(metadata.get("extension") or "").casefold().lstrip(".")
    if extension not in {"srt", "ass"}:
        return _rejected(row, "unsupported_format")
    try:
        cues = read_cues(case_root / str(item["relative_path"]), extension)
    except (OSError, UnicodeError, ValueError) as error:
        return _rejected(row, f"parse_error: {error}")
    spans = positive_cues(cues)
    if not spans:
        return _rejected(row, "no_positive_duration_cues")
    try:
        language = analyze_subtitle_language(cues)
    except Exception as error:
        return _rejected(row, f"language_analysis_failed: {error}")
    row.update(
        cue_count=len(spans),
        active_duration_s=sum(cue.end_s - cue.start_s for cue in spans),
        language=language.language.value,
        language_reason=language.reason,
        warnings=warnings,
    )
    if language.language in {
        SubtitleLanguage.NON_JAPANESE,
        SubtitleLanguage.INSUFFICIENT_TEXT,
    }:
        return _rejected(row, language.language.value)
    row.update(status="eligible", gate_reasons=[])
    if anchor_cues is None:
        row.update(selection_score=None, score_components=None)
        return row
    components = _score_components(anchor_cues, cues)
    row.update(selection_score=components["selection_score"], score_components=components)
    return row


def _score_components(
    anchor: tuple[SubtitleCue, ...], candidate: tuple[SubtitleCue, ...]
) -> dict[str, float | int]:
    anchor_spans = positive_cues(anchor)
    candidate_spans = positive_cues(candidate)
    anchor_active = sum(cue.end_s - cue.start_s for cue in anchor_spans)
    candidate_active = sum(cue.end_s - cue.start_s for cue in candidate_spans)
    active_multiplier = min(1.0, anchor_active / candidate_active)
    cue_ratio = len(candidate_spans) / len(anchor_spans)
    cue_multiplier = 1.0 if cue_ratio <= 4 / 3 else sqrt((4 / 3) / cue_ratio)
    score = subtitle_anchor_fit_score(anchor, candidate)
    return {
        "goodness_of_fit": subtitle_goodness_of_fit(anchor, candidate),
        "anchor_cue_count": len(anchor_spans),
        "candidate_cue_count": len(candidate_spans),
        "active_multiplier": active_multiplier,
        "cue_count_ratio": cue_ratio,
        "cue_multiplier": cue_multiplier,
        "selection_score": score,
    }


def _select_candidate(rows: list[dict[str, object]]) -> dict[str, object] | None:
    return next(
        (
            row for row in rows
            if row["considered"] and row.get("selection_score") is not None
        ),
        None,
    )


def _copy_selected_source(selected: dict[str, object], case_root: Path) -> str:
    source = case_root / str(selected["relative_path"])
    extension = source.suffix.casefold() or ".sub"
    destination = case_root / f"source{extension}"
    shutil.copyfile(source, destination)
    return destination.relative_to(case_root).as_posix()


def _rejected(row: dict[str, object], reason: str) -> dict[str, object]:
    row.update(
        status="rejected",
        gate_reasons=[reason],
        selection_score=None,
        score_components=None,
    )
    return row
