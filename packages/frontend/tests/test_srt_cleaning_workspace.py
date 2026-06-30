from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ja_media_frontend.srt_cleaning import commands
from ja_media_frontend.srt_cleaning.batch import build_manifest_row, build_windows, write_jsonl
from ja_media_frontend.srt_cleaning.commands import run_generate, run_reconstruct
from ja_media_frontend.srt_cleaning.contracts import SourceDocument
from ja_media_frontend.srt_cleaning.reconstruct import reconstruct_from_batch
from ja_media_frontend.srt_cleaning.workspace import run_for_anilist


SRT_TEXT = """1
00:00:01,000 --> 00:00:02,000
一

2
00:00:02,000 --> 00:00:03,000
二
"""


def generate_args(workspace_root: Path, **overrides: object) -> argparse.Namespace:
    values = {
        "anilist": "101",
        "anilist_file": None,
        "out": None,
        "workspace_root": str(workspace_root),
        "run_id": None,
        "run_hash": False,
        "model": "test-model",
        "window_size": 2,
        "context_cues": 0,
        "group_prefix": None,
        "episode_one_only": False,
        "max_requests_per_shard": 50_000,
        "max_bytes_per_shard": 200 * 1000 * 1000,
        "single_jsonl": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_generate_defaults_to_anilist_workspace_and_clobbers_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    house_style = tmp_path / "house-style.md"
    house_style.write_text("policy", encoding="utf-8")
    run = run_for_anilist(101, workspace_root=tmp_path, run_id="current")
    run.run_dir.mkdir(parents=True)
    (run.run_dir / "stale.txt").write_text("old", encoding="utf-8")

    monkeypatch.setattr(commands, "HttpKitsunekkoSubtitlesClient", lambda: StubSubtitleClient())

    run_generate(
        generate_args(tmp_path),
        house_style_path=house_style,
        fetch_metadata=lambda _id: metadata_context(),
        fetch_subtitle_inventory=lambda *_args, **_kwargs: subtitle_inventory(),
    )

    assert not (run.run_dir / "stale.txt").exists()
    assert (run.run_dir / "batch-00001.jsonl").exists()
    assert (run.run_dir / "manifest.jsonl").exists()
    assert (run.run_dir / "shards.json").exists()
    assert list((run.run_dir / "sources").glob("sub-one.*.srt"))
    manifest = json.loads((run.run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_name"] == "ja-media.srt-clean.run"
    assert manifest["paths"]["batch_shards"] == ["batch-00001.jsonl"]


def test_generate_run_hash_preserves_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    house_style = tmp_path / "house-style.md"
    house_style.write_text("policy", encoding="utf-8")
    current = run_for_anilist(101, workspace_root=tmp_path, run_id="current")
    current.run_dir.mkdir(parents=True)
    (current.run_dir / "keep.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(commands, "HttpKitsunekkoSubtitlesClient", lambda: StubSubtitleClient())

    run_generate(
        generate_args(tmp_path, run_hash=True),
        house_style_path=house_style,
        fetch_metadata=lambda _id: metadata_context(),
        fetch_subtitle_inventory=lambda *_args, **_kwargs: subtitle_inventory(),
    )

    assert (current.run_dir / "keep.txt").exists()
    hashed_runs = [
        path
        for path in current.run_dir.parent.iterdir()
        if path.name.startswith("sha256-")
    ]
    assert len(hashed_runs) == 1
    assert (hashed_runs[0] / "batch-00001.jsonl").exists()


def test_reconstruct_autodetects_workspace_paths(tmp_path: Path) -> None:
    run = run_for_anilist(101, workspace_root=tmp_path, run_id="current")
    run.run_dir.mkdir(parents=True)
    source = source_doc(run.sources_dir / "source.srt")
    windows = build_windows(
        source,
        SRT_TEXT,
        window_size=2,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )
    write_jsonl(run.manifest_path, [build_manifest_row(windows[0], model="test")])
    write_jsonl(
        run.results_path,
        [
            result_row(
                windows[0].custom_id,
                [
                    {"id": 1, "decision": "asis", "text": None, "category": None},
                    {"id": 2, "decision": "edit", "text": "二 cleaned", "category": None},
                ],
            )
        ],
    )

    run_reconstruct(
        argparse.Namespace(
            anilist=101,
            workspace_root=str(tmp_path),
            run_id="current",
            manifest=None,
            batch_output=None,
            out_dir=None,
            allow_partial=False,
            no_archive=True,
        )
    )

    cleaned = next((run.reconstruct_dir / "cleaned").glob("*.cleaned.srt"))
    assert "二 cleaned" in cleaned.read_text(encoding="utf-8")


def test_reconstruct_rejects_incompatible_manifest_schema(tmp_path: Path) -> None:
    source = source_doc(tmp_path / "source.srt")
    windows = build_windows(
        source,
        SRT_TEXT,
        window_size=2,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )
    manifest = build_manifest_row(windows[0], model="test")
    manifest["schema_version"] = "2.0.0"
    manifest_path = tmp_path / "manifest.jsonl"
    output_path = tmp_path / "results.jsonl"
    write_jsonl(manifest_path, [manifest])
    write_jsonl(output_path, [result_row(windows[0].custom_id, [])])

    with pytest.raises(ValueError, match="expected 1.1.0"):
        reconstruct_from_batch(
            batch_output_paths=[output_path],
            manifest_path=manifest_path,
            output_dir=tmp_path / "out",
            archive=False,
        )


class StubSubtitleClient:
    def file_content(self, _subtitle_id: str) -> bytes:
        return SRT_TEXT.encode("utf-8")


def metadata_context() -> SimpleNamespace:
    return SimpleNamespace(
        anilist_id=101,
        title_english="Test",
        title_native="テスト",
        title_romaji="Test",
        description=None,
        characters=[],
        metadata_warnings=[],
    )


def subtitle_inventory() -> SimpleNamespace:
    return SimpleNamespace(
        entries=[
            SimpleNamespace(
                subtitle_id="sub-one",
                repo_path="Group/Test - 01.srt",
                name="Test - 01.srt",
                is_srt=True,
            )
        ]
    )


def source_doc(path: Path) -> SourceDocument:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            "body": {"choices": [{"message": {"content": json.dumps({"decisions": decisions})}}]},
        },
    }
