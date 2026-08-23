"""Join cleaned subtitle decisions back to stable source cue identities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from ja_media_core.transcripts import read_srt

from forced_alignment_retiming.cases import CanonicalCase


def prepare_cleaned_input(
    case: CanonicalCase, reconstruct_dir: Path, case_root: Path
) -> dict[str, object]:
    """Write alignment cue records and verify them against the cleaned SRT."""

    if not case.subtitle_id or not case.subtitle_source_sha256:
        raise ValueError(f"case {case.name!r} has no pinned cleaned subtitle")
    manifests = _selected_rows(
        reconstruct_dir / "manifest.jsonl", case, "subtitle manifest"
    )
    decisions = _selected_rows(
        reconstruct_dir / "decisions.jsonl", case, "cleaning decisions"
    )
    source_path = _one_source_path(manifests)
    if _file_hash(source_path) != case.subtitle_source_sha256:
        raise RuntimeError(f"source subtitle hash changed: {source_path}")

    cue_inputs = _cue_inputs(manifests)
    decision_by_index = _decision_map(decisions)
    records, excluded = _build_records(
        read_srt(source_path), cue_inputs, decision_by_index, case
    )
    cleaned_path = _cleaned_path(
        reconstruct_dir,
        case.subtitle_source_sha256,
        source_filename=str(manifests[0]["filename"]),
    )
    _validate_reconstruction(records, read_srt(cleaned_path))

    inputs = case_root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    records_path = inputs / "input-cues.jsonl"
    records_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )
    excluded_path = inputs / "excluded-cues.jsonl"
    excluded_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in excluded),
        encoding="utf-8",
    )
    copied_srt = inputs / "cleaned.srt"
    shutil.copyfile(cleaned_path, copied_srt)
    return {
        "source_subtitle_id": case.subtitle_id,
        "source_sha256": case.subtitle_source_sha256,
        "source_cue_count": len(cue_inputs),
        "alignment_cue_count": len(records),
        "input_cues": records_path.relative_to(case_root).as_posix(),
        "excluded_cues": excluded_path.relative_to(case_root).as_posix(),
        "cleaned_srt": copied_srt.relative_to(case_root).as_posix(),
    }


def _selected_rows(path: Path, case: CanonicalCase, label: str) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = [
        row
        for row in rows
        if str(row.get("subtitle_id")) == case.subtitle_id
        and (
            str(row.get("source_sha256")) == case.subtitle_source_sha256
            or str(row.get("source_key", "")).endswith(
                f":{case.subtitle_source_sha256}"
            )
        )
    ]
    if not selected:
        raise RuntimeError(f"no {label} rows matched case {case.name!r}")
    return selected


def _one_source_path(manifests: list[dict[str, Any]]) -> Path:
    paths = {Path(str(row["local_cache_path"])).resolve() for row in manifests}
    if len(paths) != 1:
        raise RuntimeError(f"expected one source subtitle path, found {len(paths)}")
    path = paths.pop()
    if not path.is_file():
        raise RuntimeError(f"source subtitle is missing: {path}")
    return path


def _cue_inputs(manifests: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    inputs: dict[int, dict[str, Any]] = {}
    for window in manifests:
        indexes = window["active_indexes"]
        originals = window["active_original_texts"]
        mechanical = window["active_texts"]
        rules = window["active_rules"]
        flags = window["active_flags"]
        if len({len(indexes), len(originals), len(mechanical), len(rules), len(flags)}) != 1:
            raise RuntimeError(f"window arrays disagree: {window['custom_id']}")
        for index, original, baseline, cue_rules, cue_flags in zip(
            indexes, originals, mechanical, rules, flags, strict=True
        ):
            source_index = int(index)
            if source_index in inputs:
                raise RuntimeError(f"source cue {source_index} occurs in two windows")
            inputs[source_index] = {
                "original_text": str(original),
                "mechanical_text": str(baseline),
                "mechanical_rules": list(cue_rules),
                "flags": list(cue_flags),
            }
    return inputs


def _decision_map(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    decisions: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not row.get("compliant", False) or row.get("index") is None:
            continue
        index = int(row["index"])
        if index in decisions:
            raise RuntimeError(f"source cue {index} has two cleaning decisions")
        decisions[index] = row
    return decisions


def _build_records(cues, inputs, decisions, case) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cue_by_index = {cue.index: cue for cue in cues}
    if set(cue_by_index) != set(inputs):
        raise RuntimeError("cleaning manifest cue indexes do not match source SRT")
    records = []
    excluded = []
    for source_index in sorted(inputs):
        cue_input = inputs[source_index]
        decision = decisions.get(source_index)
        baseline = cue_input["mechanical_text"]
        action = str(decision["decision"]) if decision else "mechanical"
        cue = cue_by_index[source_index]
        base_record = {
            "schema_name": "ja-media.forced-alignment.input-cue",
            "schema_version": "1.0.0",
            "cue_id": f"{case.subtitle_source_sha256}:cue:{source_index}",
            "source_index": source_index,
            "source_start_s": cue.start_s,
            "source_end_s": cue.end_s,
            "original_text": cue_input["original_text"],
            "mechanical_text": baseline,
            "cleaning_decision": action,
            "cleaning_reasons": list(decision.get("reasons") or []) if decision else [],
            "mechanical_rules": cue_input["mechanical_rules"],
            "flags": cue_input["flags"],
        }
        if not baseline or action == "remove":
            excluded.append({**base_record, "exclusion_reason": action if baseline else "empty"})
            continue
        text = str(decision.get("text") or "") if action == "edit" else baseline
        if not text:
            continue
        records.append(
            {
                **base_record,
                "cleaned_index": len(records) + 1,
                "alignment_text": text,
            }
        )
    return records, excluded


def _cleaned_path(
    reconstruct_dir: Path, source_hash: str, *, source_filename: str
) -> Path:
    """Select the reconstruction owned by this catalog row and cleaning run."""

    stem = Path(source_filename).stem
    expected = reconstruct_dir / "cleaned" / f"{stem}.{source_hash[:12]}.cleaned.srt"
    if not expected.is_file():
        raise RuntimeError(f"reconstructed SRT is missing: {expected}")
    return expected


def _validate_reconstruction(records: list[dict[str, Any]], cleaned_cues) -> None:
    actual = [(cue.index, cue.start_s, cue.end_s, cue.text) for cue in cleaned_cues]
    expected = [
        (row["cleaned_index"], row["source_start_s"], row["source_end_s"], row["alignment_text"])
        for row in records
    ]
    if actual != expected:
        raise RuntimeError("input cue records do not reproduce the cleaned SRT")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
