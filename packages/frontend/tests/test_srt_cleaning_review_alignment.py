"""Focused checks for loading candidate timings into cleaning review."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from ja_media_core.transcripts import SubtitleCue
from ja_media_frontend.srt_cleaning.review_alignment import read_alignment_case
from ja_media_frontend.srt_cleaning.review_models import (
    ReviewAlignment,
    ReviewCue,
    ReviewSource,
    ReviewWorkspace,
)
from ja_media_frontend.srt_cleaning.review_rule_overlay import (
    cue_needs_review,
    render_cue_panel,
)


def test_alignment_case_links_source_index_and_changes_playback_clock(
    tmp_path: Path,
) -> None:
    case = tmp_path / "case.json"
    case.write_text(
        json.dumps({"cleaned_subtitle": {"source_sha256": "abc123"}})
    )
    results = tmp_path / "full-alignment" / "results.json"
    results.parent.mkdir()
    results.write_text(
        json.dumps(
            {
                "windows": [
                    {
                        "cues": [
                            {
                                "source_index": 7,
                                "aligned_start_s": 12.5,
                                "aligned_end_s": 14.0,
                                "status": "aligned",
                                "token_count": 3,
                                "alignment_window_index": 4,
                                "alignment_window_kind": "boundary",
                                "alignment_candidate_count": 2,
                            }
                        ]
                    }
                ]
            }
        )
    )

    loaded = read_alignment_case(case)
    alignment = loaded["by_source_index"][7]
    cue = ReviewCue(
        original=SubtitleCue(None, 7, 10.0, 11.0, "話す"),
        decision=None,
        mechanical_text="話す",
        mechanically_changed=False,
        alignment=alignment,
    )

    assert loaded["source_sha256"] == "abc123"
    assert cue.playback_cue.start_s == 12.5
    assert cue.playback_cue.end_s == 14.0
    assert alignment.window_index == 4
    assert alignment.window_kind == "boundary"
    assert alignment.candidate_count == 2


def test_suspicious_alignment_is_visible_and_part_of_flagged_walk(tmp_path: Path) -> None:
    case = tmp_path / "case.json"
    case.write_text(json.dumps({"cleaned_subtitle": {"source_sha256": "abc123"}}))
    results = tmp_path / "full-alignment" / "results.json"
    results.parent.mkdir()
    results.write_text(
        json.dumps(
            {
                "windows": [
                    {
                        "cues": [
                            {
                                "source_index": 7,
                                "aligned_start_s": 0.0,
                                "aligned_end_s": 18.0,
                                "status": "suspicious",
                                "token_count": 3,
                                "score_signals": {
                                    "min_endpoint_max_probability": 0.01,
                                    "min_top_two_probability_margin": 0.0,
                                    "max_normalized_entropy": 0.8,
                                    "min_edge_distance_s": 0.0,
                                    "zero_duration_token_count": 1,
                                    "reversed_token_count": 1,
                                    "repeated_timestamp_count": 2,
                                    "backward_token_count": 1,
                                },
                            }
                        ]
                    }
                ]
            }
        )
    )
    alignment = read_alignment_case(case)["by_source_index"][7]
    cue = ReviewCue(
        original=SubtitleCue(None, 7, 10.0, 11.0, "話す"),
        decision=None,
        mechanical_text="話す",
        mechanically_changed=False,
        alignment=alignment,
    )

    assert cue_needs_review(cue)
    console = Console(record=True, width=140)
    console.print(render_cue_panel(cue, playing=False, rule_overlay=False))
    rendered = console.export_text()
    assert "Space plays (forced alignment): 00:00.000 -> 00:18.000" in rendered
    assert "Moved from original: start 10.00s earlier; end 7.00s later" in rendered
    assert "NEEDS TIMING REVIEW" in rendered
    assert "1 text piece ends before it starts" in rendered
    assert "not a confidence result" in rendered
    assert "zero/reverse/repeat/back" not in rendered


def test_workspace_prefers_aligned_source_for_episode(tmp_path: Path) -> None:
    excluded = ReviewCue(
        original=SubtitleCue(None, 1, 1.0, 2.0, "話す"),
        decision=None,
        mechanical_text="話す",
        mechanically_changed=False,
    )
    aligned = ReviewCue(
        original=SubtitleCue(None, 2, 2.0, 3.0, "聞く"),
        decision=None,
        mechanical_text="聞く",
        mechanically_changed=False,
        alignment=ReviewAlignment(2.1, 2.9, "aligned"),
    )
    sources = tuple(
        ReviewSource(
            anilist_id=57,
            subtitle_id=f"sub-{index}",
            repo_path=f"sub-{index}.srt",
            filename=f"sub-{index}.srt",
            source_path=tmp_path / f"sub-{index}.srt",
            cleaned_path=None,
            episode_number=1,
            source_sha256=f"sha-{index}",
            cues=(excluded, aligned) if index == 1 else (excluded,),
            alignment_path=(tmp_path / "results.json") if index == 1 else None,
        )
        for index in range(2)
    )
    workspace = ReviewWorkspace(57, "run", tmp_path, sources)

    assert workspace.preferred_source_index(57, 1) == 1
    assert workspace.preferred_cue_indices() == {"sub-1": 1}
