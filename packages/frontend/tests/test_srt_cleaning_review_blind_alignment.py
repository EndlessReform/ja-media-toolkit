from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ja_media_frontend.srt_cleaning.review_audio import ReviewAudio
from ja_media_frontend.srt_cleaning.review_blind_alignment import (
    BlindAlignmentReviewScreen,
    render_blind_pair,
)
from ja_media_frontend.srt_cleaning.review_loader import load_review_workspace
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
