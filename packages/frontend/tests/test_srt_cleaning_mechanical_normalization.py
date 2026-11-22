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
    assert "asis" not in enum
    assert manifest["active_original_texts"][0] == "私は求められている\n王塚真唯像を➡"
    assert manifest["active_texts"][0] == "私は求められている王塚真唯像を"


def test_batch_prompt_and_manifest_include_deterministic_preclean(
    tmp_path: Path,
) -> None:
    text = SRT_TEXT.replace(
        "私は求められている\n王塚真唯像を➡", "（先生）今日は２人･一緒だ―"
    )
    source = source_doc(tmp_path / "source.srt")
    source.source_path.write_text(text, encoding="utf-8")
    window = build_windows(
        source,
        text,
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
    manifest = build_manifest_row(window, model="test-model")

    assert "今日は2人・一緒だ" in row["body"]["messages"][1]["content"]
    assert manifest["active_rules"][0] == [
        "strip_parenthesized_span",
        "strip_terminal_horizontal_bar",
        "halfwidth_alphanumeric",
        "normalize_middle_dot",
    ]


def test_reconstruct_normalizes_model_noop_edit_to_as_is(tmp_path: Path) -> None:
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
                    {"id": 1, "decision": "as_is", "text": None, "reasons": []},
                    {
                        "id": 2,
                        "decision": "edit",
                        "text": "こんな訳のわからないことを…。",
                        "reasons": ["punctuation"],
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

    decisions = [
        json.loads(line)
        for line in summary.decisions_path.read_text(encoding="utf-8").splitlines()
    ]
    assert summary.cleaned_srts == 1
    assert summary.errors == 0
    assert decisions[1]["decision"] == "as_is"
    assert decisions[1]["text"] is None
    assert decisions[1]["reasons"] == []


def test_reconstruct_recovers_noop_edit_from_saved_validation_attempt(
    tmp_path: Path,
) -> None:
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
    saved = result_row(
        window.custom_id,
        [
            {"id": 1, "decision": "as_is", "text": None, "reasons": []},
            {
                "id": 2,
                "decision": "edit",
                "text": "こんな訳のわからないことを…。",
                "reasons": ["punctuation"],
            },
        ],
    )
    write_jsonl(
        result_path,
        [
            {
                "custom_id": window.custom_id,
                "error": {
                    "error_kind": "validation_retry_exhausted",
                    "message": "edit did not change text",
                    "attempts": [
                        {
                            "error_kind": "decision_validation_error",
                            "response": saved["response"],
                        }
                    ],
                },
            }
        ],
    )

    summary = reconstruct_from_batch(
        batch_output_paths=[result_path],
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
        archive=False,
    )

    assert summary.cleaned_srts == 1
    assert summary.skipped_sources == 0
    assert summary.errors == 0


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
