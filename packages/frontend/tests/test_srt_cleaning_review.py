from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from ja_media_frontend.srt_cleaning.batch import read_jsonl, write_jsonl
from ja_media_frontend.srt_cleaning.cli import build_parser
from ja_media_frontend.srt_cleaning.review_audio import ReviewAudio
from ja_media_frontend.srt_cleaning.review_clipboard import review_sample_payload
from ja_media_frontend.srt_cleaning.review_dialogs import ReasonStatsModal, RuleStatsModal
from ja_media_frontend.srt_cleaning.review_formatting import (
    colored_model_diff,
    timeline_styles,
)
from ja_media_frontend.srt_cleaning.review_loader import (
    load_review_directory,
    load_review_workspace,
)
from ja_media_frontend.srt_cleaning.review_reason_pivot import (
    ReasonPivotExplorer,
    build_reason_pivot,
)
from ja_media_frontend.srt_cleaning.review_tui import SrtCleaningReviewApp
from ja_media_frontend.srt_cleaning.review_stats import summarize_reasons
from srt_cleaning_review_fixtures import prepared_run
from textual.widgets import ContentSwitcher


def test_review_workspace_joins_manifest_sources_and_decisions(tmp_path: Path) -> None:
    run = prepared_run(tmp_path)

    workspace = load_review_workspace(run)

    source = workspace.sources[0]
    assert workspace.anilist_id == 101
    assert source.episode_number == 1
    assert source.cleaned_path is not None
    assert source.cues[0].decision is not None
    assert source.cues[0].decision.kind == "edit"
    assert source.cues[0].decision.served_model == "test/model"
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


def test_review_formatting_marks_changes_and_alternates_decision_shades(
    tmp_path: Path,
) -> None:
    workspace = load_review_workspace(prepared_run(tmp_path))
    cue = workspace.sources[0].cues[0]
    removed_cue = workspace.sources[0].cues[1]

    before, after = colored_model_diff(cue).renderables
    _removed_before, removed_after = colored_model_diff(removed_cue).renderables

    assert before.plain == "normalized input\n一"
    assert after.plain == "cleaned diff\n一 cleaned"
    assert any("bright_green" in str(span.style) for span in after.spans)
    assert any("red" in str(span.style) for span in removed_after.spans)
    assert timeline_styles((cue, cue)) == ("#f59f00", "#ffd43b")


def test_review_reason_stats_split_edit_and_remove_counts(tmp_path: Path) -> None:
    workspace = load_review_workspace(prepared_run(tmp_path))

    stats = summarize_reasons(workspace.sources)

    assert (stats.cue_count, stats.edits, stats.removes) == (3, 1, 1)
    assert [
        (row.reason, row.edits, row.removes) for row in stats.reasons
    ] == [("noise", 0, 1), ("ocr", 1, 0)]


def test_reason_pivot_counts_and_percent_denominator(tmp_path: Path) -> None:
    workspace = load_review_workspace(prepared_run(tmp_path))
    pivot = build_reason_pivot(workspace)

    assert pivot.changed_cues == 2
    assert [row.reason for row in pivot.rows] == ["noise", "ocr"]
    assert [len(row.matches) for row in pivot.rows] == [1, 1]
    assert (pivot.rows[0].edits, pivot.rows[0].removes) == (0, 1)
    assert (pivot.rows[1].edits, pivot.rows[1].removes) == (1, 0)
    assert (pivot.rows[0].series, pivot.rows[0].episodes, pivot.rows[0].sources) == (
        1,
        1,
        1,
    )


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


def test_review_tui_opens_reason_stats_modal(tmp_path: Path) -> None:
    async def run_app() -> tuple[type, type]:
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
            await pilot.press("s")
            opened = type(app.screen)
            await pilot.press("s")
            return opened, type(app.screen)

    opened, closed = asyncio.run(run_app())

    assert opened is ReasonStatsModal
    assert closed is not ReasonStatsModal


def test_review_tui_toggles_rule_overlay_and_opens_scores(tmp_path: Path) -> None:
    async def run_app() -> tuple[bool, str, type]:
        workspace = load_review_workspace(prepared_run(tmp_path))
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
            await pilot.press("r")
            panel_title = app.render_diff().title
            await pilot.press("R")
            return app.rule_overlay, panel_title, type(app.screen)

    overlay, panel_title, screen = asyncio.run(run_app())

    assert overlay
    assert panel_title == "Original vs cleaned — rule overlay"
    assert screen is RuleStatsModal


def test_review_tui_jumps_forward_and_backward_between_flags(tmp_path: Path) -> None:
    async def run_app() -> tuple[int, int]:
        workspace = load_review_workspace(prepared_run(tmp_path))
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
            "ja_media_frontend.srt_cleaning.review_rule_overlay.cue_has_candidate_flags",
            return_value=True,
        ):
            async with app.run_test() as pilot:
                await pilot.press("f")
                forward = app.cue_index(app.source)
                await pilot.press("F")
                return forward, app.cue_index(app.source)

    assert asyncio.run(run_app()) == (1, 0)


def test_review_tui_switches_f5_f6_tabs(tmp_path: Path) -> None:
    async def run_app() -> tuple[str | None, str | None, int, int, str | None]:
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
            switcher = app.query_one("#review-views", ContentSwitcher)
            initial = switcher.current
            await pilot.press("f6")
            pivot = switcher.current
            detail_height = app.query_one("#reason-pivot-detail").region.height
            await pilot.press("j")
            row_index = app.query_one("#reason-pivot", ReasonPivotExplorer).row_index
            await pilot.press("f5")
            return initial, pivot, detail_height, row_index, switcher.current

    assert asyncio.run(run_app()) == (
        "cue-review",
        "reason-pivot",
        10,
        1,
        "cue-review",
    )


def test_review_directory_and_tui_cross_series_boundaries(tmp_path: Path) -> None:
    async def run_app() -> tuple[tuple[tuple[int, int], ...], int, str, int | None]:
        workspace = load_review_directory(prepared_corpus_review(tmp_path))
        app = SrtCleaningReviewApp(
            workspace=workspace,
            series_label="First Series",
            initial_anilist_id=101,
            initial_episode=1,
            audio_profile="portable-aac-v1",
            manual_audio=None,
            initial_audio=ReviewAudio(None, "audio unavailable"),
            audio_loader=lambda _episode: ReviewAudio(None, "audio unavailable"),
        )
        async with app.run_test() as pilot:
            await pilot.press("]")
            cue = app.current_cue
            return (
                workspace.episode_keys,
                app.anilist_id,
                cue.original.text if cue else "",
                app.query_one("#episodes").index,
            )

    keys, anilist_id, cue_text, rail_index = asyncio.run(run_app())

    assert keys == ((101, 1), (202, 2))
    assert anilist_id == 202
    assert cue_text == "三"
    assert rail_index == 1


def test_review_parser_accepts_reconstructed_run_directory() -> None:
    args = build_parser().parse_args(["review", "--run-dir", "/tmp/reconstruct"])

    assert args.run_dir == "/tmp/reconstruct"
    assert args.anilist is None


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


def test_review_tui_jumps_between_non_accept_cues(tmp_path: Path) -> None:
    async def run_app() -> tuple[int, int]:
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
            await pilot.press("n")
            next_index = app.current_cue.original.index
            await pilot.press("N")
            previous_index = app.current_cue.original.index
            return next_index, previous_index

    assert asyncio.run(run_app()) == (2, 1)


def prepared_corpus_review(tmp_path: Path) -> Path:
    run = prepared_run(tmp_path)
    manifest_rows = read_jsonl(run.manifest_path)
    decision_rows = read_jsonl(run.reconstruct_dir / "decisions.jsonl")
    for row in manifest_rows:
        if row["subtitle_id"] == "sub-two":
            row["anilist_id"] = 202
    for row in decision_rows:
        if row["subtitle_id"] == "sub-two":
            row["anilist_id"] = 202
            source_hash = str(row["source_key"]).split(":", 2)[2]
            row["source_key"] = f"202:{row['subtitle_id']}:{source_hash}"
    corpus_root = tmp_path / "corpus"
    reconstruct_dir = corpus_root / "luna.reconstruct"
    write_jsonl(corpus_root / "clean.manifest.jsonl", manifest_rows)
    write_jsonl(reconstruct_dir / "decisions.jsonl", decision_rows)
    return reconstruct_dir
