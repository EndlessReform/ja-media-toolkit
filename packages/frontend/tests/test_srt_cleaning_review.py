from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from ja_media_frontend.srt_cleaning.batch import build_manifest_row, build_windows, write_jsonl
from ja_media_frontend.srt_cleaning.contracts import SourceDocument
from ja_media_frontend.srt_cleaning.review_audio import ReviewAudio
from ja_media_frontend.srt_cleaning.review_clipboard import review_sample_payload
from ja_media_frontend.srt_cleaning.review_loader import load_review_workspace
from ja_media_frontend.srt_cleaning.review_tui import SrtCleaningReviewApp
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


def test_review_workspace_joins_manifest_sources_and_decisions(tmp_path: Path) -> None:
    run = prepared_run(tmp_path)

    workspace = load_review_workspace(run)

    source = workspace.sources[0]
    assert workspace.anilist_id == 101
    assert source.episode_number == 1
    assert source.cleaned_path is not None
    assert source.cues[0].decision is not None
    assert source.cues[0].decision.kind == "edit"
    assert source.cues[0].display_text == "一 cleaned"
    assert source.cues[1].decision is not None
    assert source.cues[1].decision.kind == "remove"


def test_review_workspace_resolves_sources_from_current_run_dir(
    tmp_path: Path,
) -> None:
    run = prepared_run(tmp_path, stale_manifest_source_path=True)

    workspace = load_review_workspace(run)

    assert workspace.sources[0].source_path.is_file()
    assert workspace.sources[0].source_path.parent == run.sources_dir


def test_review_tui_pages_between_discovered_episodes(tmp_path: Path) -> None:
    async def run_app() -> tuple[int, str]:
        run = prepared_run(tmp_path)
        workspace = load_review_workspace(run)
        app = SrtCleaningReviewApp(
            workspace=workspace,
            series_label="Test Series",
            initial_episode=1,
            audio_profile="portable-aac-v1",
            manual_audio=None,
            initial_audio=ReviewAudio(None, "audio unavailable"),
            audio_loader=lambda _episode: ReviewAudio(None, "audio unavailable"),
        )
        async with app.run_test() as pilot:
            await pilot.press("]")
            cue = app.current_cue
            return app.episode_number, cue.original.text if cue else ""

    episode, cue_text = asyncio.run(run_app())

    assert episode == 2
    assert cue_text == "三"


def test_review_sample_payload_identifies_source_window_and_text(
    tmp_path: Path,
) -> None:
    run = prepared_run(tmp_path)
    workspace = load_review_workspace(run)
    source = workspace.sources[0]
    cue = source.cues[0]

    payload = review_sample_payload(workspace=workspace, source=source, cue=cue)

    assert payload["schema_name"] == "ja-media.srt-clean.review-sample"
    assert payload["anilist_id"] == 101
    assert payload["subtitle_id"] == "sub-one"
    assert payload["source_sha256"]
    assert payload["cue"]["index"] == 1
    assert payload["cue"]["original"] == "一"
    assert payload["cue"]["cleaned"] == "一 cleaned"
    assert payload["decision"]["custom_id"]
    assert payload["decision"]["local_id"] == 1
    assert payload["decision"]["window_number"] == 1


def test_review_tui_c_copies_current_sample(tmp_path: Path) -> None:
    async def run_app() -> tuple[bool, str]:
        run = prepared_run(tmp_path)
        workspace = load_review_workspace(run)
        app = SrtCleaningReviewApp(
            workspace=workspace,
            series_label="Test Series",
            initial_episode=1,
            audio_profile="portable-aac-v1",
            manual_audio=None,
            initial_audio=ReviewAudio(None, "audio unavailable"),
            audio_loader=lambda _episode: ReviewAudio(None, "audio unavailable"),
        )
        with patch(
            "ja_media_frontend.srt_cleaning.review_interaction.copy_review_sample"
        ) as copy:
            async with app.run_test() as pilot:
                await pilot.press("c")
                return copy.called, app._clipboard_status

    copied, status = asyncio.run(run_app())

    assert copied
    assert status == "copied review sample"


def prepared_run(tmp_path: Path, *, stale_manifest_source_path: bool = False):
    run = run_for_anilist(101, workspace_root=tmp_path, run_id="current")
    run.run_dir.mkdir(parents=True)
    first = source_doc(run.sources_dir / "episode-one.srt", "sub-one", EPISODE_ONE)
    second = source_doc(run.sources_dir / "episode-two.srt", "sub-two", EPISODE_TWO)
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
            manifest["local_cache_path"] = (
                f"/home/ritsuko/elsewhere/sources/{Path(str(manifest['local_cache_path'])).name}"
            )
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
            decision_row(manifests[0], 1, 1, "edit", "一 cleaned", "ocr"),
            decision_row(manifests[0], 2, 2, "remove", None, "noise"),
            decision_row(manifests[1], 1, 1, "asis", None, None),
        ],
    )
    clean_dir = run.reconstruct_dir / "cleaned"
    clean_dir.mkdir(parents=True)
    (clean_dir / cleaned_srt_name(manifests[0])).write_text("", encoding="utf-8")
    return run


def source_doc(path: Path, subtitle_id: str, text: str) -> SourceDocument:
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


def decision_row(
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
        "within_active_span": True,
        "compliant": True,
        "noncompliant_reasons": [],
    }
