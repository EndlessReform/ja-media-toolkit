"""Checks for the cleaning-to-alignment cue join."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from forced_alignment_retiming.cases import CanonicalCase
from forced_alignment_retiming.cleaned_input import prepare_cleaned_input


def test_prepares_stable_cues_and_reproduces_cleaned_srt(tmp_path: Path) -> None:
    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n（人）話す\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n（音）\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\n元の文\n"
    )
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    reconstruct = tmp_path / "reconstruct"
    (reconstruct / "cleaned").mkdir(parents=True)
    manifest = {
        "custom_id": "w1",
        "anilist_id": 57,
        "subtitle_id": "sub-1",
        "source_sha256": source_hash,
        "source_key": f"57:sub-1:{source_hash}",
        "local_cache_path": str(source),
        "active_indexes": [1, 2, 3],
        "active_original_texts": ["（人）話す", "（音）", "元の文"],
        "active_texts": ["話す", "", "元の文"],
        "active_rules": [["strip"], ["strip"], []],
        "active_flags": [[], [], []],
    }
    _write_jsonl(reconstruct / "manifest.jsonl", [manifest])
    decisions = [
        _decision(source_hash, 1, "as_is"),
        _decision(source_hash, 2, "remove"),
        _decision(source_hash, 3, "edit", "直した文"),
    ]
    _write_jsonl(reconstruct / "decisions.jsonl", decisions)
    cleaned = reconstruct / "cleaned" / f"fixture.{source_hash[:12]}.cleaned.srt"
    cleaned.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n話す\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\n直した文\n"
    )
    case = CanonicalCase(
        name="fixture",
        namespace="anilist",
        series_id="57",
        episode="1",
        subtitle_id="sub-1",
        subtitle_source_sha256=source_hash,
    )

    result = prepare_cleaned_input(case, reconstruct, tmp_path / "case")

    rows = [
        json.loads(line)
        for line in (tmp_path / "case/inputs/input-cues.jsonl").read_text().splitlines()
    ]
    assert result["alignment_cue_count"] == 2
    assert [row["source_index"] for row in rows] == [1, 3]
    assert [row["cleaned_index"] for row in rows] == [1, 2]
    assert rows[1]["alignment_text"] == "直した文"
    assert rows[0]["cue_id"] == f"{source_hash}:cue:1"


def _decision(source_hash: str, index: int, action: str, text=None) -> dict:
    return {
        "subtitle_id": "sub-1",
        "source_key": f"57:sub-1:{source_hash}",
        "index": index,
        "decision": action,
        "text": text,
        "reasons": [],
        "compliant": True,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
