from __future__ import annotations

import json
from pathlib import Path

from ja_media_frontend.srt_cleaning.batch import build_manifest_row, build_windows, write_jsonl
from ja_media_frontend.srt_cleaning.contracts import SourceDocument
from ja_media_frontend.srt_cleaning.reconstruct import reconstruct_from_batch


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
"""


def source_doc(path: Path, subtitle_id: str = "sub-one") -> SourceDocument:
    path.write_text(SRT_TEXT, encoding="utf-8")
    return SourceDocument(
        anilist_id=101,
        subtitle_id=subtitle_id,
        repo_path=f"Group/{subtitle_id}.srt",
        filename=f"{subtitle_id}.srt",
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


def api_error_row(custom_id: str, status_code: int, message: str) -> dict[str, object]:
    return {
        "custom_id": custom_id,
        "response": {
            "status_code": status_code,
            "body": {"error": {"message": message}},
        },
    }


def build_manifest_and_windows(source: SourceDocument) -> tuple[Path, list[object]]:
    windows = build_windows(
        source,
        SRT_TEXT,
        window_size=2,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )
    return source.source_path, windows


def test_reconstruct_uses_custom_ids_not_batch_order(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "source.srt")
    _, windows = build_manifest_and_windows(source)
    manifest_path = tmp_path / "manifest.jsonl"
    output_path = tmp_path / "batch-output.jsonl"
    write_jsonl(manifest_path, [build_manifest_row(window, model="test") for window in windows])
    write_jsonl(
        output_path,
        [
            result_row(
                windows[1].custom_id,
                [
                    {"id": 1, "decision": "remove", "text": None, "category": "noise"},
                    {"id": 2, "decision": "escalate", "text": None, "category": "unclear"},
                ],
            ),
            result_row(
                windows[0].custom_id,
                [
                    {"id": 1, "decision": "asis", "text": None, "category": None},
                    {"id": 2, "decision": "edit", "text": "二 cleaned", "category": "ocr"},
                ],
            ),
        ],
    )

    summary = reconstruct_from_batch(
        batch_output_paths=[output_path],
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
        archive=False,
    )

    cleaned = next((tmp_path / "out" / "cleaned").glob("*.cleaned.srt"))
    assert summary.cleaned_srts == 1
    assert summary.errors == 0
    assert cleaned.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:02,000\n一\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n二 cleaned\n\n"
        "3\n00:00:04,000 --> 00:00:05,000\n四\n"
    )
    decisions = [
        json.loads(line)
        for line in summary.decisions_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["id"] for row in decisions] == [1, 2, 1, 2]
    assert [row["index"] for row in decisions] == [1, 2, 3, 4]


def test_failed_span_goes_to_dlq_without_stopping_other_sources(tmp_path: Path) -> None:
    good = source_doc(tmp_path / "good.srt", subtitle_id="good")
    bad = source_doc(tmp_path / "bad.srt", subtitle_id="bad")
    good_windows = build_manifest_and_windows(good)[1]
    bad_windows = build_manifest_and_windows(bad)[1]
    manifest_path = tmp_path / "manifest.jsonl"
    output_path = tmp_path / "batch-output.jsonl"
    write_jsonl(
        manifest_path,
        [
            *(build_manifest_row(window, model="test") for window in good_windows),
            *(build_manifest_row(window, model="test") for window in bad_windows),
        ],
    )
    write_jsonl(
        output_path,
        [
            result_row(
                good_windows[0].custom_id,
                [
                    {"id": 1, "decision": "asis", "text": None, "category": None},
                    {"id": 2, "decision": "asis", "text": None, "category": None},
                ],
            ),
            result_row(
                good_windows[1].custom_id,
                [
                    {"id": 1, "decision": "asis", "text": None, "category": None},
                    {"id": 2, "decision": "asis", "text": None, "category": None},
                ],
            ),
            api_error_row(bad_windows[0].custom_id, 429, "rate limited"),
            api_error_row(bad_windows[1].custom_id, 401, "proxy auth failed"),
        ],
    )

    summary = reconstruct_from_batch(
        batch_output_paths=[output_path],
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
        archive=False,
    )

    assert summary.cleaned_srts == 1
    assert summary.skipped_sources == 1
    errors = [
        json.loads(line)
        for line in summary.errors_path.read_text(encoding="utf-8").splitlines()
    ]
    dlq = [
        json.loads(line)
        for line in summary.dlq_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["status_code"] for row in errors if "status_code" in row} == {401, 429}
    assert any(row["retryable"] is True and row["status_code"] == 429 for row in dlq)
    assert any(row["retryable"] is False and row["status_code"] == 401 for row in dlq)


def test_id_mismatch_blocks_source_and_is_reported(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "source.srt")
    _, windows = build_manifest_and_windows(source)
    manifest_path = tmp_path / "manifest.jsonl"
    output_path = tmp_path / "batch-output.jsonl"
    write_jsonl(manifest_path, [build_manifest_row(window, model="test") for window in windows])
    write_jsonl(
        output_path,
        [
            result_row(
                windows[0].custom_id,
                [
                    {"id": 1, "decision": "asis", "text": None, "category": None},
                    {"id": 99, "decision": "asis", "text": None, "category": None},
                ],
            ),
            result_row(
                windows[1].custom_id,
                [
                    {"id": 1, "decision": "asis", "text": None, "category": None},
                    {"id": 2, "decision": "asis", "text": None, "category": None},
                ],
            ),
        ],
    )

    summary = reconstruct_from_batch(
        batch_output_paths=[output_path],
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
        archive=False,
    )

    assert summary.cleaned_srts == 0
    assert not list((tmp_path / "out" / "cleaned").glob("*.srt"))
    errors = [
        json.loads(line)
        for line in summary.errors_path.read_text(encoding="utf-8").splitlines()
    ]
    decisions = [
        json.loads(line)
        for line in summary.decisions_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["error_kind"] for row in errors} >= {"id_mismatch", "missing_decision"}
    assert [row["id"] for row in decisions] == [1, 99, 1, 2]
    assert [row["index"] for row in decisions] == [1, None, 3, 4]
    assert any(
        row["id"] == 99
        and row["compliant"] is False
        and row["noncompliant_reasons"] == ["id_mismatch"]
        for row in decisions
    )


def test_duplicate_result_blocks_source_because_order_would_be_ambiguous(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "source.srt")
    _, windows = build_manifest_and_windows(source)
    manifest_path = tmp_path / "manifest.jsonl"
    output_path = tmp_path / "batch-output.jsonl"
    write_jsonl(manifest_path, [build_manifest_row(window, model="test") for window in windows])
    first_window = result_row(
        windows[0].custom_id,
        [
            {"id": 1, "decision": "asis", "text": None, "category": None},
            {"id": 2, "decision": "asis", "text": None, "category": None},
        ],
    )
    write_jsonl(
        output_path,
        [
            first_window,
            first_window,
            result_row(
                windows[1].custom_id,
                [
                    {"id": 1, "decision": "asis", "text": None, "category": None},
                    {"id": 2, "decision": "asis", "text": None, "category": None},
                ],
            ),
        ],
    )

    summary = reconstruct_from_batch(
        batch_output_paths=[output_path],
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
        archive=False,
    )

    errors = [
        json.loads(line)
        for line in summary.errors_path.read_text(encoding="utf-8").splitlines()
    ]
    assert summary.cleaned_srts == 0
    assert any(row["error_kind"] == "duplicate_result" for row in errors)
