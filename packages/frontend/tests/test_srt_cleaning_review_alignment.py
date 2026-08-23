"""Focused checks for loading candidate timings into cleaning review."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from ja_media_core.transcripts import SubtitleCue
from ja_media_frontend.srt_cleaning.review_audio import ReviewAudio
from ja_media_frontend.srt_cleaning.review_alignment import (
    read_alignment_case,
    read_alignment_cases,
)
from ja_media_frontend.srt_cleaning.review_models import (
    ReviewAlignment,
    ReviewCue,
    ReviewSource,
    ReviewWorkspace,
)
from ja_media_frontend.srt_cleaning.review_loader import load_review_workspace
from ja_media_frontend.srt_cleaning.review_rule_overlay import (
    cue_needs_review,
    render_cue_panel,
)
from ja_media_frontend.srt_cleaning.review_tui import SrtCleaningReviewApp
from srt_cleaning_review_fixtures import prepared_run


def test_alignment_case_links_source_index_and_changes_playback_clock(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "audio" / "source.ac3"
    audio.parent.mkdir()
    audio.write_bytes(b"prepared audio")
    case = tmp_path / "case.json"
    case.write_text(
        json.dumps(
            {
                "cleaned_subtitle": {
                    "source_subtitle_id": "sub-1",
                    "source_sha256": "abc123",
                },
                "audio": {"relative_path": "audio/source.ac3"},
            }
        )
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
    assert loaded["audio_path"] == audio
    assert cue.playback_cue.start_s == 12.5
    assert cue.playback_cue.end_s == 14.0
    assert cue.cue_with_timing(use_alignment=False).start_s == 10.0
    assert cue.cue_with_timing(use_alignment=False).end_s == 11.0
    assert alignment.window_index == 4
    assert alignment.window_kind == "boundary"
    assert alignment.candidate_count == 2


def test_suspicious_alignment_is_visible_and_part_of_flagged_walk(tmp_path: Path) -> None:
    audio = tmp_path / "audio" / "source.ac3"
    audio.parent.mkdir()
    audio.write_bytes(b"prepared audio")
    case = tmp_path / "case.json"
    case.write_text(
        json.dumps(
            {
                "cleaned_subtitle": {
                    "source_subtitle_id": "sub-1",
                    "source_sha256": "abc123",
                },
                "audio": {"relative_path": "audio/source.ac3"},
            }
        )
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
    assert "Forced-aligned borders: 00:00.000 -> 00:18.000  ACTIVE" in rendered
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


def test_review_selects_each_source_prepared_audio_without_service_lookup(
    tmp_path: Path,
) -> None:
    loaded = load_review_workspace(prepared_run(tmp_path))
    first_audio = tmp_path / "first.ac3"
    second_audio = tmp_path / "second.ac3"
    first = replace(
        loaded.sources[0],
        alignment_path=tmp_path / "first-results.json",
        alignment_audio_path=first_audio,
    )
    second = replace(
        loaded.sources[0],
        subtitle_id="second-candidate",
        alignment_path=tmp_path / "second-results.json",
        alignment_audio_path=second_audio,
    )
    workspace = ReviewWorkspace(
        anilist_id=101,
        run_id="prepared-audio",
        run_dir=tmp_path,
        sources=(first, second),
    )
    app = SrtCleaningReviewApp(
        workspace=workspace,
        series_label="Test Series",
        initial_episode=1,
        audio_profile="portable-aac-v1",
        manual_audio=None,
        initial_audio=ReviewAudio(None, "not loaded"),
    )

    with patch(
        "ja_media_frontend.srt_cleaning.review_interaction.load_review_audio",
        return_value=ReviewAudio(None, "loaded prepared audio"),
    ) as load_audio:
        app._default_audio_loader(1)
        app.source_index = 1
        app._default_audio_loader(1)

    assert [call.kwargs["manual_audio"] for call in load_audio.call_args_list] == [
        first_audio,
        second_audio,
    ]
    assert all(
        call.kwargs["manual_audio_status"] == "using prepared alignment audio"
        for call in load_audio.call_args_list
    )


def test_alignment_slice_loads_each_case_by_catalog_identity(tmp_path: Path) -> None:
    cases = []
    for index in (1, 2):
        root = tmp_path / f"case-{index}"
        root.mkdir()
        audio = root / "audio" / "source.ac3"
        audio.parent.mkdir()
        audio.write_bytes(f"prepared audio {index}".encode())
        case = root / "case.json"
        case.write_text(
            json.dumps(
                {
                    "cleaned_subtitle": {
                        "source_subtitle_id": f"sub-{index}",
                        "source_sha256": f"hash-{index}",
                    },
                    "audio": {"relative_path": "audio/source.ac3"},
                }
            )
        )
        results = root / "full-alignment" / "results.json"
        results.parent.mkdir()
        results.write_text(json.dumps({"selected_cues": [], "windows": []}))
        cases.append({"case_manifest": str(case)})
    slice_path = tmp_path / "slice.json"
    slice_path.write_text(
        json.dumps(
            {
                "schema_name": "ja-media.forced-alignment.prepared-slice",
                "cases": cases,
            }
        )
    )

    loaded = read_alignment_cases(slice_path)

    assert set(loaded) == {("sub-1", "hash-1"), ("sub-2", "hash-2")}
    assert loaded[("sub-1", "hash-1")]["audio_path"] == (
        tmp_path / "case-1" / "audio" / "source.ac3"
    )
    assert loaded[("sub-2", "hash-2")]["audio_path"] == (
        tmp_path / "case-2" / "audio" / "source.ac3"
    )
