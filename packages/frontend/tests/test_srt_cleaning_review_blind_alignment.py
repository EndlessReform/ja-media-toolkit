from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from ja_media_frontend.srt_cleaning.review_audio import ReviewAudio
from ja_media_frontend.srt_cleaning.review_blind_alignment import (
    BlindAlignmentReviewScreen,
    render_blind_pair,
)
from ja_media_frontend.srt_cleaning.review_loader import load_review_workspace
from ja_media_frontend.srt_cleaning.review_shift_alignment import (
    ShiftAlignmentReviewScreen,
    render_shift_target,
)
from ja_media_frontend.srt_cleaning.review_tui import SrtCleaningReviewApp
from rich.console import Console
from srt_cleaning_review_fixtures import prepared_run


def test_f7_opens_blind_candidates_without_exposing_window_size(
    tmp_path: Path,
) -> None:
    async def run_app() -> tuple[str, Path]:
        report_path = _write_report(tmp_path)
        app = _app(tmp_path, report_path)
        async with app.run_test() as pilot:
            await pilot.press("f7")
            await pilot.pause()
            assert isinstance(app.screen, BlindAlignmentReviewScreen)
            console = Console(record=True, width=100)
            console.print(
                render_blind_pair(app.screen.pair, index=0, total=1, saved_choice=None)
            )
            rendered = console.export_text()
            await pilot.press("a")
            return rendered, report_path.parent / "blind-judgments.jsonl"

    rendered, judgments_path = asyncio.run(run_app())

    assert "候補の台詞です" in rendered
    assert "Candidate A" in rendered
    assert "Candidate B" in rendered
    assert "60" not in rendered
    assert "180" not in rendered
    saved = json.loads(judgments_path.read_text(encoding="utf-8"))
    assert saved["choice"] == "A better"
    assert saved["target_cue_id"] == "cue:7"


def test_f7_edge_report_explains_and_exposes_all_six_playable_timings(
    tmp_path: Path,
) -> None:
    async def run_app() -> str:
        report_path = _write_shift_report(tmp_path)
        app = _app(tmp_path, report_path)
        async with app.run_test() as pilot:
            await pilot.press("f7")
            await pilot.pause()
            assert isinstance(app.screen, ShiftAlignmentReviewScreen)
            console = Console(record=True, width=180)
            console.print(
                render_shift_target(
                    app.screen.target,
                    app.screen.results,
                    index=0,
                    total=1,
                )
            )
            return console.export_text()

    rendered = asyncio.run(run_app())

    assert "ordinary dialogue that previously broke" in rendered
    assert "60s • cue near" in rendered
    assert "[6] 180s • cue near" in rendered
    assert "BROKEN order" in rendered
    assert "start 0.50s earlier; end 0.50s" in rendered
    assert "Nearest crop edge" in rendered


def test_edge_report_plays_exact_test_and_original_intervals(tmp_path: Path) -> None:
    report = json.loads(_write_shift_report(tmp_path).read_text())
    player = RecordingPlayer()
    screen = ShiftAlignmentReviewScreen(report, player=player)  # type: ignore[arg-type]

    screen._play_result(0)
    screen._play_original()

    assert player.calls[0] == pytest.approx((9.5, 1.0))
    assert player.calls[1] == pytest.approx((9.8, 1.4))


def _app(tmp_path: Path, report_path: Path) -> SrtCleaningReviewApp:
    workspace = load_review_workspace(prepared_run(tmp_path / "review"))
    return SrtCleaningReviewApp(
        workspace=workspace,
        series_label="Test Series",
        initial_episode=1,
        audio_profile="portable-aac-v1",
        manual_audio=None,
        initial_audio=ReviewAudio(None, "audio unavailable"),
        audio_loader=lambda _episode: ReviewAudio(None, "audio unavailable"),
        alignment_eval_path=report_path,
    )


def _write_report(tmp_path: Path) -> Path:
    path = tmp_path / "case" / "stability" / "results.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_name": "ja-media.forced-alignment.stability",
                "schema_version": "1.0.0",
                "case": "test-case",
                "source_sha256": "a" * 64,
                "blind_pairs": [
                    {
                        "target_cue_id": "cue:7",
                        "source_index": 7,
                        "text": "候補の台詞です",
                        "candidates": {
                            "A": {
                                "window_size_s": 60,
                                "start_s": 10.0,
                                "end_s": 11.0,
                            },
                            "B": {
                                "window_size_s": 180,
                                "start_s": 10.1,
                                "end_s": 11.1,
                            },
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_shift_report(tmp_path: Path) -> Path:
    path = tmp_path / "case" / "stability" / "results.json"
    path.parent.mkdir(parents=True)
    results = []
    for window_s in (60, 180):
        for position, offset, status in (
            ("beginning", -0.5, "suspicious"),
            ("middle", 0.0, "aligned"),
            ("end", 0.25, "aligned"),
        ):
            results.append(
                {
                    "target_cue_id": "cue:7",
                    "window_size_s": window_s,
                    "position_name": position,
                    "crop_start_s": 0.0,
                    "crop_end_s": 60.0 if window_s == 60 else 180.0,
                    "target_result": {
                        "source_index": 7,
                        "text": "候補の台詞です",
                        "aligned_start_s": 10.0 + offset,
                        "aligned_end_s": 11.0 + offset,
                        "status": status,
                        "score_signals": {"min_edge_distance_s": 1.5},
                    },
                }
            )
    path.write_text(
        json.dumps(
            {
                "schema_name": "ja-media.forced-alignment.stability",
                "schema_version": "1.0.0",
                "experiment_kind": "explicit-edge-position",
                "case": "test-case",
                "source_sha256": "a" * 64,
                "targets": [
                    {
                        "cue_id": "cue:7",
                        "source_index": 7,
                        "text": "候補の台詞です",
                        "source_start_s": 9.8,
                        "source_end_s": 11.2,
                        "cohort": "reviewed_interior_broken",
                    }
                ],
                "results": results,
                "blind_pairs": [{"target_cue_id": "cue:7"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


class RecordingPlayer:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []

    def play(self, start_s: float, duration_s: float) -> None:
        self.calls.append((start_s, duration_s))

    def stop(self) -> None:
        pass
