from __future__ import annotations

import json
from pathlib import Path

from ja_media_frontend.srt_cleaning.batch import (
    build_batch_row,
    build_manifest_row,
    build_windows,
    write_jsonl,
)
from ja_media_frontend.srt_cleaning.contracts import SourceDocument
from ja_media_frontend.srt_cleaning.normalization import mechanically_normalize_text
from ja_media_frontend.srt_cleaning.reconstruct import reconstruct_from_batch


SRT_TEXT = """1
00:00:01,000 --> 00:00:02,000
私は求められている
王塚真唯像を➡

2
00:00:02,000 --> 00:00:03,000
こんな訳のわからないことを…。
"""


def test_mechanical_normalization_joins_lines_and_strips_trailing_arrow() -> None:
    normalized = mechanically_normalize_text("私は求められている\n王塚真唯像を➡")

    assert normalized.text == "私は求められている王塚真唯像を"
    assert normalized.changed is True
    assert normalized.rules == ("join_physical_lines", "strip_trailing_arrow")


def test_batch_prompt_uses_as_is_and_model_visible_baseline(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "source.srt")
    window = build_windows(
        source,
        SRT_TEXT,
        window_size=2,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )[0]
    row = build_batch_row(
        window,
        model="test-model",
        policy_text="policy",
        series_context="AniList ID: 101",
    )

    prompt = row["body"]["messages"][1]["content"]
    enum = row["body"]["response_format"]["json_schema"]["schema"]["$defs"]
    enum = enum["CleanDecision"]["properties"]["decision"]["enum"]
    manifest = build_manifest_row(window, model="test")

    assert "私は求められている王塚真唯像を" in prompt
    assert "王塚真唯像を➡" not in prompt
    assert "Use decision as_is" in prompt
    assert "as_is" in enum
    assert "asis" in enum
    assert manifest["active_original_texts"][0] == "私は求められている\n王塚真唯像を➡"
    assert manifest["active_texts"][0] == "私は求められている王塚真唯像を"


def test_reconstruct_keeps_model_noop_edit_visible(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "source.srt")
    window = build_windows(
        source,
        SRT_TEXT,
        window_size=2,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )[0]
    manifest_path = tmp_path / "manifest.jsonl"
    result_path = tmp_path / "results.jsonl"
    write_jsonl(manifest_path, [build_manifest_row(window, model="test")])
    write_jsonl(
        result_path,
        [
            result_row(
                window.custom_id,
                [
                    {"id": 1, "decision": "as_is", "text": None, "category": None},
                    {
                        "id": 2,
                        "decision": "edit",
                        "text": "こんな訳のわからないことを…。",
                        "category": None,
                    },
                ],
            )
        ],
    )

    summary = reconstruct_from_batch(
        batch_output_paths=[result_path],
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
        archive=False,
    )

    cleaned = next((tmp_path / "out" / "cleaned").glob("*.cleaned.srt"))
    decisions = [
        json.loads(line)
        for line in summary.decisions_path.read_text(encoding="utf-8").splitlines()
    ]
    assert "私は求められている王塚真唯像を" in cleaned.read_text(encoding="utf-8")
    assert decisions[0]["decision"] == "as_is"
    assert decisions[0]["mechanically_changed"] is True
    assert decisions[1]["decision"] == "edit"
    assert decisions[1]["model_text_matches_mechanical"] is True


def source_doc(path: Path) -> SourceDocument:
    path.write_text(SRT_TEXT, encoding="utf-8")
    return SourceDocument(
        anilist_id=101,
        subtitle_id="sub-one",
        repo_path="Group/Test - 01.srt",
        filename="Test - 01.srt",
        source_path=path,
    )


def result_row(custom_id: str, decisions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "custom_id": custom_id,
        "response": {
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"decisions": decisions},
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        },
    }
