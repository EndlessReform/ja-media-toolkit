from __future__ import annotations

from pathlib import Path

from ja_media_frontend.srt_cleaning.batch import (
    build_manifest_row,
    build_windows,
    write_jsonl,
)
from ja_media_frontend.srt_cleaning.contracts import SourceDocument
from ja_media_frontend.srt_cleaning.source_rebuild import cleaned_srt_name
from ja_media_frontend.srt_cleaning.workspace import run_for_anilist, write_run_manifest


EPISODE_ONE = """1
00:00:01,000 --> 00:00:02,000
一

2
00:00:02,000 --> 00:00:03,000
二
"""

EPISODE_TWO = """1
00:00:04,000 --> 00:00:05,000
三
"""


def prepared_run(tmp_path: Path, *, stale_manifest_source_path: bool = False):
    run = run_for_anilist(101, workspace_root=tmp_path, run_id="current")
    run.run_dir.mkdir(parents=True)
    first = _source_doc(run.sources_dir / "episode-one.srt", "sub-one", EPISODE_ONE)
    second = _source_doc(run.sources_dir / "episode-two.srt", "sub-two", EPISODE_TWO)
    first_windows = build_windows(
        first,
        EPISODE_ONE,
        window_size=2,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )
    second_windows = build_windows(
        second,
        EPISODE_TWO,
        window_size=2,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )
    manifests = [
        *(build_manifest_row(window, model="test") for window in first_windows),
        *(build_manifest_row(window, model="test") for window in second_windows),
    ]
    if stale_manifest_source_path:
        for manifest in manifests:
            filename = Path(str(manifest["local_cache_path"])).name
            manifest["local_cache_path"] = f"/elsewhere/sources/{filename}"
    write_jsonl(run.manifest_path, manifests)
    write_run_manifest(
        run,
        batch_shards=[run.run_dir / "batch-00001.jsonl"],
        model="test",
        pipeline_version="clean:v1",
        prompt_policy_sha256="a" * 64,
    )
    write_jsonl(
        run.reconstruct_dir / "decisions.jsonl",
        [
            _decision_row(manifests[0], 1, 1, "edit", "一 cleaned", "ocr"),
            _decision_row(manifests[0], 2, 2, "remove", None, "noise"),
            _decision_row(manifests[1], 1, 1, "asis", None, None),
        ],
    )
    clean_dir = run.reconstruct_dir / "cleaned"
    clean_dir.mkdir(parents=True)
    (clean_dir / cleaned_srt_name(manifests[0])).write_text("", encoding="utf-8")
    return run


def _source_doc(path: Path, subtitle_id: str, text: str) -> SourceDocument:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    episode = "01" if subtitle_id == "sub-one" else "02"
    return SourceDocument(
        anilist_id=101,
        subtitle_id=subtitle_id,
        repo_path=f"Group/Test - {episode}.srt",
        filename=f"Test - {episode}.srt",
        source_path=path,
    )


def _decision_row(
    manifest: dict[str, object],
    local_id: int,
    index: int,
    decision: str,
    text: str | None,
    category: str | None,
) -> dict[str, object]:
    return {
        "custom_id": manifest["custom_id"],
        "source_key": (
            f"{manifest['anilist_id']}:{manifest['subtitle_id']}:"
            f"{manifest['source_sha256']}"
        ),
        "anilist_id": manifest["anilist_id"],
        "subtitle_id": manifest["subtitle_id"],
        "repo_path": manifest["repo_path"],
        "window_number": manifest["window_number"],
        "result_position": local_id,
        "id": local_id,
        "index": index,
        "decision": decision,
        "text": text,
        "category": category,
        "served_model": "test/model",
        "within_active_span": True,
        "compliant": True,
        "noncompliant_reasons": [],
    }
