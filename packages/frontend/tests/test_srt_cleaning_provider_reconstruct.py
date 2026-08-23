from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from ja_media_frontend.srt_cleaning.batch import build_manifest_row, build_windows, write_jsonl
from ja_media_frontend.srt_cleaning.contracts import SourceDocument
from ja_media_frontend.srt_cleaning.provider_reconstruct import reconstruct_provider_output
from ja_media_frontend.srt_cleaning.review_loader import load_review_directory


def test_limited_provider_run_reconstructs_only_selected_windows(tmp_path: Path) -> None:
    source_path = tmp_path / "source.srt"
    source_text = (
        "1\n00:00:01,000 --> 00:00:02,000\n（先生）一\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n二\n"
    )
    source_path.write_text(source_text, encoding="utf-8")
    source = SourceDocument(101, "sub", "Group/E01.srt", "E01.srt", source_path)
    windows = build_windows(
        source,
        source_text,
        window_size=1,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )
    manifest_path = tmp_path / "manifest.jsonl"
    output_path = tmp_path / "luna.results.jsonl"
    write_jsonl(manifest_path, [build_manifest_row(window, model="test") for window in windows])
    write_jsonl(output_path, [_result(windows[0].custom_id)])

    output_dir = reconstruct_provider_output(
        manifest_path=manifest_path,
        output_path=output_path,
        request_ids={windows[0].custom_id},
        limited=True,
        console=Console(file=None),
    )

    assert output_dir.name == "luna.smoke-reconstruct"
    smoke_manifest = output_dir / "manifest.jsonl"
    assert len(smoke_manifest.read_text().splitlines()) == 1
    cleaned = next((output_dir / "cleaned").glob("*.srt"))
    assert "（先生）" not in cleaned.read_text(encoding="utf-8")
    write_jsonl(tmp_path / "unrelated.manifest.jsonl", [])
    assert len(load_review_directory(output_dir).sources) == 1


def _result(custom_id: str) -> dict[str, object]:
    content = json.dumps(
        {"decisions": [{"id": 1, "decision": "as_is", "text": None, "reasons": []}]}
    )
    return {
        "custom_id": custom_id,
        "response": {
            "status_code": 200,
            "body": {"choices": [{"message": {"content": content}}]},
        },
    }
