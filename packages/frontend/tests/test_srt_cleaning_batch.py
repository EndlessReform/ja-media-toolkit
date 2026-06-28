from __future__ import annotations

import json
from pathlib import Path

import pytest

from ja_media_frontend.srt_cleaning.batch import (
    build_batch_row,
    build_manifest_row,
    build_windows,
    prefix_artifact_path,
    render_window_prompt,
    write_batch_shards,
)
from ja_media_frontend.srt_cleaning.commands import render_series_context
from ja_media_frontend.srt_cleaning.contracts import SourceDocument
from ja_media_frontend.srt_cleaning.result_parser import parse_batch_result_row


SRT_TEXT = """1
00:00:01,000 --> 00:00:02,000
一

2
00:00:02,000 --> 00:00:03,000
二

3
00:00:03,000 --> 00:00:04,000
三

4
00:00:04,000 --> 00:00:05,000
四

5
00:00:05,000 --> 00:00:06,000
五
"""


def source_doc(path: Path) -> SourceDocument:
    path.write_text(SRT_TEXT, encoding="utf-8")
    return SourceDocument(
        anilist_id=101,
        subtitle_id="sub/one",
        repo_path="Group/episode01.srt",
        filename="episode01.srt",
        source_path=path,
    )


def test_build_windows_are_non_overlapping_with_context_and_stable_ids(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "episode01.srt")

    windows = build_windows(
        source,
        SRT_TEXT,
        window_size=2,
        context_cues=1,
        prompt_policy_sha256="a" * 64,
    )
    again = build_windows(
        source,
        SRT_TEXT,
        window_size=2,
        context_cues=1,
        prompt_policy_sha256="a" * 64,
    )

    assert [window.active_indexes for window in windows] == [(1, 2), (3, 4), (5,)]
    assert [tuple(cue.index for cue in window.before) for window in windows] == [(), (2,), (4,)]
    assert [tuple(cue.index for cue in window.after) for window in windows] == [(3,), (5,), ()]
    assert [window.custom_id for window in windows] == [window.custom_id for window in again]
    assert "srt-sub-one" in windows[0].custom_id


def test_window_prompt_omits_surrounding_context_when_window_has_none(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "episode01.srt")

    window = build_windows(
        source,
        SRT_TEXT,
        window_size=2,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )[0]

    prompt = render_window_prompt(window, series_context="AniList ID: 101")

    assert '<context_before count="' not in prompt
    assert '<context_after count="' not in prompt
    assert "<active>" in prompt
    assert '<cue id="1" start="1.000" end="2.000">一</cue>' in prompt
    assert '<cue id="2" start="2.000" end="3.000">二</cue>' in prompt
    assert '<cue id="3"' not in prompt
    assert "[1]" not in prompt


def test_window_prompt_renders_context_without_actionable_ids(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "episode01.srt")

    window = build_windows(
        source,
        SRT_TEXT,
        window_size=2,
        context_cues=1,
        prompt_policy_sha256="a" * 64,
    )[1]

    prompt = render_window_prompt(window, series_context="AniList ID: 101")

    assert '<context_before count="1">' in prompt
    assert '<context_after count="1">' in prompt
    assert '<context_before count="1">\n<cue start="2.000" end="3.000">二</cue>' in prompt
    assert '<context_after count="1">\n<cue start="5.000" end="6.000">五</cue>' in prompt
    assert '<cue id="1" start="3.000" end="4.000">三</cue>' in prompt
    assert '<cue id="2" start="4.000" end="5.000">四</cue>' in prompt


def test_batch_schema_uses_local_id_not_source_index(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "episode01.srt")
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

    schema = row["body"]["response_format"]["json_schema"]["schema"]
    decision = schema["$defs"]["CleanDecision"]
    assert "id" in decision["properties"]
    assert "index" not in decision["properties"]
    assert "id" in decision["required"]


def test_write_batch_shards_respects_request_limits(tmp_path: Path) -> None:
    rows = [{"custom_id": f"row-{index}", "body": {"n": index}} for index in range(5)]

    shards = write_batch_shards(
        rows,
        output_prefix=tmp_path / "batch",
        max_requests_per_shard=2,
        max_bytes_per_shard=10_000,
    )

    assert [shard.request_count for shard in shards] == [2, 2, 1]
    assert [path.name for path in sorted(tmp_path.glob("*.jsonl"))] == [
        "batch.batch-00001.jsonl",
        "batch.batch-00002.jsonl",
        "batch.batch-00003.jsonl",
    ]


def test_write_batch_shards_single_jsonl(tmp_path: Path) -> None:
    rows = [{"custom_id": f"row-{index}", "body": {"n": index}} for index in range(5)]

    shards = write_batch_shards(
        rows,
        output_prefix=tmp_path / "batch",
        max_requests_per_shard=2,
        max_bytes_per_shard=10_000,
        single_jsonl=True,
    )

    assert [shard.request_count for shard in shards] == [5]
    assert [path.name for path in sorted(tmp_path.glob("*.jsonl"))] == [
        "batch.batch-00001.jsonl",
    ]


def test_prefix_artifact_path_appends_to_dotted_prefix(tmp_path: Path) -> None:
    assert (
        prefix_artifact_path(tmp_path / "batch.v1", ".manifest.jsonl").name
        == "batch.v1.manifest.jsonl"
    )


def test_write_batch_shards_rejects_single_request_over_byte_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceeds shard byte limit"):
        write_batch_shards(
            [{"custom_id": "huge", "body": {"text": "x" * 100}}],
            output_prefix=tmp_path / "batch",
            max_requests_per_shard=10,
            max_bytes_per_shard=20,
        )


def test_series_context_uses_native_character_names_before_romaji() -> None:
    class Context:
        anilist_id = 101573
        title_english = "Bloom Into You"
        title_native = "やがて君になる"
        title_romaji = "Yagate Kimi ni Naru"
        description = None
        characters = [
            {
                "node": {
                    "name": {
                        "full": "Touko Nanami",
                        "native": "七海燈子",
                        "alternative": ["Nanami Touko"],
                    }
                }
            },
            {"node": {"name": {"full": "Yuu Koito", "native": "小糸侑"}}},
        ]

    context = render_series_context(Context())

    assert "Characters: 七海燈子 (Touko Nanami / Nanami Touko), 小糸侑 (Yuu Koito)" in context
    assert "Characters: Touko Nanami, Yuu Koito" not in context


def test_result_parser_classifies_auth_errors_as_non_retryable(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "episode01.srt")
    window = build_windows(
        source,
        SRT_TEXT,
        window_size=5,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )[0]
    manifest = build_manifest_row(window, model="test-model")
    row = {
        "custom_id": window.custom_id,
        "response": {
            "status_code": 401,
            "body": {"error": {"message": "nope"}},
        },
    }

    parsed = parse_batch_result_row(row, manifests={window.custom_id: manifest})

    assert parsed["error"]["error_kind"] == "auth_error"
    assert parsed["error"]["retryable"] is False
    assert parsed["error"]["message"] == "nope"


def clean_result_row(custom_id: str, decisions: list[dict[str, object]]) -> dict[str, object]:
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
