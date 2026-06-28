from __future__ import annotations

import json
from pathlib import Path

import pytest

from ja_media_frontend.srt_cleaning.batch import (
    build_manifest_row,
    build_windows,
    prefix_artifact_path,
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
        window_cues=2,
        context_cues=1,
        prompt_policy_sha256="a" * 64,
    )
    again = build_windows(
        source,
        SRT_TEXT,
        window_cues=2,
        context_cues=1,
        prompt_policy_sha256="a" * 64,
    )

    assert [window.active_indexes for window in windows] == [(1, 2), (3, 4), (5,)]
    assert [tuple(cue.index for cue in window.before) for window in windows] == [(), (2,), (4,)]
    assert [tuple(cue.index for cue in window.after) for window in windows] == [(3,), (5,), ()]
    assert [window.custom_id for window in windows] == [window.custom_id for window in again]
    assert "srt-sub-one" in windows[0].custom_id


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
        window_cues=5,
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
