import asyncio
import json
from pathlib import Path

from ja_media_core.transcripts import SubtitleCue
from textual.app import App
from textual.widgets import TextArea

from subtitle_alignment.review_data import (
    ReviewCase,
    Variant,
    _variant_sort_key,
    append_judgment,
    track_stats,
)
from subtitle_alignment.review_audio import _audio_object_key
from subtitle_alignment.review_full_tracks import (
    FullTrackComparisonModal,
    format_full_track,
)
from subtitle_alignment.review_interaction import AlignmentReviewInteractionMixin
from subtitle_alignment import cli


def test_track_stats_make_sparse_anchor_evidence_explicit() -> None:
    cues = (
        SubtitleCue(None, 1, 10.0, 12.0, "one"),
        SubtitleCue(None, 2, 100.0, 103.0, "two"),
    )

    stats = track_stats(cues)

    assert stats.cues == 2
    assert stats.active_s == 5.0
    assert stats.span_s == 93.0


def test_full_track_text_retains_every_cue_and_timestamp() -> None:
    cues = (
        SubtitleCue(None, 10, 1.0, 2.5, "first"),
        SubtitleCue(None, 20, 65.0, 67.0, "second\nline"),
    )

    rendered = format_full_track(cues)

    assert "[0001]  00:01.000 → 00:02.500\nfirst" in rendered
    assert "[0002]  01:05.000 → 01:07.000\nsecond\nline" in rendered


def test_full_track_modal_mounts_both_independent_panes() -> None:
    async def run() -> None:
        cue = SubtitleCue(None, 1, 1.0, 2.0, "anchor body")
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(
                FullTrackComparisonModal(
                    anchor=(cue,),
                    candidate=(SubtitleCue(None, 1, 3.0, 4.0, "candidate body"),),
                    anchor_name="anchor.srt",
                    candidate_name="candidate.ass",
                )
            )
            await pilot.pause()
            assert app.screen.query_one("#full-anchor", TextArea).text.endswith(
                "anchor body"
            )
            assert app.screen.query_one("#full-candidate", TextArea).text.endswith(
                "candidate body"
            )
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, FullTrackComparisonModal)

    asyncio.run(run())


def test_stale_playback_tick_cannot_claim_missing_audio_is_ready() -> None:
    class Harness(AlignmentReviewInteractionMixin):
        episode_key = (2, 1)
        _player = None
        _audio_key = (1, 1)
        _audio_status = "ready"
        _playback_poll = None

        def refresh_view(self) -> None:
            pass

    harness = Harness()

    harness._playback_tick()

    assert harness._audio_status == "A load audio"
    assert harness._audio_key is None


def test_variants_pin_identity_then_rank_scored_methods_descending() -> None:
    def variant(method: str, score: float | None, order: int) -> Variant:
        return Variant(
            pair_id="pair", method=method, method_order=order,
            output_path=f"{method}.srt", status="scored", score=score, gain=None,
            scale=None, median_offset_s=None, min_offset_s=None, max_offset_s=None,
            max_abs_offset_s=None, offset_blocks=None,
            offset_bound_exceeded=False, block_starts_s=(),
        )

    variants = [
        variant("weak", 0.2, 1), variant("unscored", None, 2),
        variant("identity", 0.1, 0), variant("strong", 0.9, 3),
    ]

    assert [item.method for item in sorted(variants, key=_variant_sort_key)] == [
        "identity", "strong", "weak", "unscored",
    ]


def test_judgments_are_append_only_and_joinable(tmp_path) -> None:
    variant = Variant(
        pair_id="pair", method="alass-global", method_order=1,
        output_path="out.srt", status="scored", score=0.5, gain=0.2,
        scale=1.0, median_offset_s=60.0, min_offset_s=60.0,
        max_offset_s=60.0, max_abs_offset_s=60.0, offset_blocks=1,
        offset_bound_exceeded=True, block_starts_s=(60.0,),
    )
    case = ReviewCase(
        pair_id="pair", anilist_id=1, episode=2, anchor_path="anchor.srt",
        anchor_format="subrip", candidate_path="candidate.srt",
        candidate_format="subrip", candidate_repo_path="series/file.srt",
        identity_decile=1, variants=(variant,),
    )
    path = tmp_path / "annotations.jsonl"

    append_judgment(path, case, variant, "anchor_sparse")
    append_judgment(path, case, variant, "needs_audio")

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["label"] for row in rows] == ["anchor_sparse", "needs_audio"]
    assert all(row["pair_id"] == "pair" for row in rows)


def test_bronze_audio_key_is_sibling_of_metadata_directory() -> None:
    assert _audio_object_key(
        "audio/anime/bronze/186/metadata/show.json", "show.ac3"
    ) == "audio/anime/bronze/186/show.ac3"


def test_result_path_resolves_from_repository_root(tmp_path, monkeypatch) -> None:
    research_root = tmp_path / "research" / "subtitle-alignment"
    result = research_root / "output" / "gate1-matrix"
    result.mkdir(parents=True)
    (result / "gate1-matrix.duckdb").touch()
    monkeypatch.setattr(cli, "RESEARCH_ROOT", research_root)
    monkeypatch.chdir(tmp_path)

    assert cli._resolve_result(Path("output/gate1-matrix")) == result
