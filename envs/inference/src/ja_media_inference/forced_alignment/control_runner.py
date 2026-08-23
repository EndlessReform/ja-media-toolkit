"""Run small absent-text controls against one prepared episode."""

from __future__ import annotations

import json
from pathlib import Path
import re

from ja_media_inference.forced_alignment.case_runner import (
    _midpoint,
    _read_jsonl,
    select_targets,
)
from ja_media_inference.forced_alignment.qwen3_adapter_client import Qwen3AdapterClient
from ja_media_inference.forced_alignment.window_execution import align_remote_window


ABSENT_TEXT = "量子色彩の宇宙船が透明な砂漠を泳いでいる"


def run_absent_text_controls(case_manifest: Path, *, base_url: str) -> Path:
    """Compare one present cue with shifted, substituted, inserted, and SFX text."""

    case_root = case_manifest.parent
    case = json.loads(case_manifest.read_text(encoding="utf-8"))
    records = _read_jsonl(case_root / case["cleaned_subtitle"]["input_cues"])
    excluded = _read_jsonl(case_root / case["cleaned_subtitle"]["excluded_cues"])
    duration_s = float(case["audio"]["duration_s"])
    target = select_targets(records, duration_s)["middle"]
    crop_start_s = max(0.0, min(_midpoint(target) - 30.0, duration_s - 60.0))
    crop_end_s = crop_start_s + 60.0
    members = [row for row in records if crop_start_s <= _midpoint(row) < crop_end_s]
    aligner = Qwen3AdapterClient(base_url=base_url)
    audio_id = aligner.cache_audio(case["audio"])

    arms = []
    arms.append(
        _run_arm(
            aligner,
            audio_id,
            members,
            target,
            "present",
            True,
            crop_start_s,
            crop_end_s,
        )
    )
    shifted_start = min(duration_s - 60.0, crop_start_s + 45.0)
    arms.append(
        _run_arm(
            aligner,
            audio_id,
            members,
            target,
            "shifted_audio",
            False,
            shifted_start,
            shifted_start + 60.0,
        )
    )
    distant = max(records, key=lambda row: abs(_midpoint(row) - _midpoint(target)))
    arms.append(
        _run_arm(
            aligner,
            audio_id,
            _replace_target(members, target, distant["alignment_text"]),
            target,
            "unrelated_substitution",
            False,
            crop_start_s,
            crop_end_s,
        )
    )
    arms.append(
        _run_arm(
            aligner,
            audio_id,
            _replace_target(members, target, ABSENT_TEXT),
            target,
            "inserted_absent_phrase",
            False,
            crop_start_s,
            crop_end_s,
        )
    )
    nonspoken = _nonspoken_control(excluded, duration_s)
    sfx_start = max(0.0, min(_midpoint(nonspoken) - 15.0, duration_s - 30.0))
    sfx_record = {
        **nonspoken,
        "cleaned_index": 0,
        "alignment_text": _plain_control_text(nonspoken["original_text"]),
    }
    arms.append(
        _run_arm(
            aligner,
            audio_id,
            [sfx_record],
            sfx_record,
            "nonspoken_label",
            False,
            sfx_start,
            sfx_start + 30.0,
        )
    )
    destination = case_root / "confidence-controls" / "results.json"
    destination.write_text(
        json.dumps(
            {
                "schema_name": "ja-media.forced-alignment.absent-text-controls",
                "schema_version": "1.0.0",
                "case": case["case"],
                "arms": arms,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return destination


def _run_arm(aligner, audio, records, target, label, text_present, start_s, end_s):
    result = align_remote_window(
        aligner,
        audio,
        records,
        target_id=str(target["cue_id"]),
        target_name=label,
        crop_start_s=start_s,
        crop_end_s=end_s,
    )
    return {
        "label": label,
        "text_present": text_present,
        "target_text": result["target_result"]["text"],
        "target_result": result["target_result"],
        "request_elapsed_s": result["request_elapsed_s"],
    }


def _replace_target(records, target, text):
    return [
        {**row, "alignment_text": text} if row["cue_id"] == target["cue_id"] else row
        for row in records
    ]


def _nonspoken_control(excluded, duration_s):
    candidates = [
        row
        for row in excluded
        if re.search(r"音|声|演奏|音楽|チャイム|鳴き", str(row["original_text"]))
    ]
    if not candidates:
        raise RuntimeError("no excluded sound/music cue is available for a control")
    return min(candidates, key=lambda row: abs(_midpoint(row) - duration_s / 2))


def _plain_control_text(value: str) -> str:
    text = re.sub(r"^[（(]|[）)]$", "", value.strip())
    return text or "音楽"
